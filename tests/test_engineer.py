from __future__ import annotations

import math

import numpy as np
import pytest

from simengineer.core.config import EngineerConfig
from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import Flags
from simengineer.engineer.engineer import RaceEngineer
from simengineer.engineer.fuel import FuelModel, race_laps_remaining
from simengineer.engineer.gaps import compute_gaps
from simengineer.engineer.pit import pit_window
from simengineer.engineer.pit_commands import (
    DisabledPitCommander,
    RecordingPitCommander,
    make_pit_commander,
)
from simengineer.engineer.tyres import TyreModel
from simengineer.engineer.weather import WeatherMonitor
from simengineer.sources.scenarios import LAP_TIME, get_scenario
from simengineer.sources.synthetic import Scenario, SyntheticSource
from simengineer.telemetry.laps import Lap, LapSegmenter
from simengineer.voice.arbiter import Utterance


def lap(n: int, used: float, time: float = 90.0, **flags: bool) -> Lap:
    return Lap(n, time, 0.0, 4000.0, {}, fuel_start=50.0, fuel_end=50.0 - used, **flags)


def frame(**kw: object) -> Frame:
    base: dict[str, object] = dict(
        tick=0, session_time=0.0, lap=1, lap_dist_pct=0.0, speed=50.0, throttle=1.0,
        brake=0.0, steering=0.0,
    )  # fmt: skip
    base.update(kw)
    return Frame(**base)  # type: ignore[arg-type]


RACE = SessionInfo(session_type="Race", fuel_capacity_l=100.0, track_length_m=4000.0)


# --- carburant
def test_fuel_average_uses_only_clean_laps_in_window() -> None:
    m = FuelModel(window=3, margin_laps=1.0, reserve_l=0.0)
    assert m.status(40.0, 10.0).avg_per_lap_l is None
    assert m.add_lap(lap(1, 2.0))
    assert not m.add_lap(lap(2, 9.0, pit=True))  # arrêt au stand
    assert not m.add_lap(lap(3, 1.0, yellow=True))  # neutralisation
    assert not m.add_lap(lap(4, 2.0, complete=False))
    assert not m.add_lap(lap(5, -20.0))  # ravitaillement
    assert m.add_lap(lap(6, 3.0, offtrack=True))  # hors-piste : conso représentative
    assert m.avg_per_lap == 2.5
    for n in range(7, 10):
        m.add_lap(lap(n, 4.0))
    assert m.avg_per_lap == 4.0 and m.clean_laps == 3  # fenêtre glissante


def test_fuel_status_margin_and_reserve() -> None:
    m = FuelModel(window=5, margin_laps=1.0, reserve_l=0.5)
    m.add_lap(lap(1, 2.5))
    st = m.status(20.5, 6.0)
    assert st.laps_in_tank == 8.0 and st.margin_laps == 2.0
    assert st.fuel_needed_l == 18.0 and st.fuel_to_add_l == 0.0 and st.can_finish
    st = m.status(20.5, 10.0)
    assert st.fuel_to_add_l == 7.5 and st.can_finish is False and st.margin_laps == -2.0
    practice = m.status(20.5, None)
    assert practice.laps_in_tank == 8.0 and practice.can_finish is None


def test_laps_remaining_lap_race_timed_race_and_practice() -> None:
    f = frame(lap_dist_pct=0.25, session_laps_remain=10)
    assert race_laps_remaining(f, RACE, 92.0) == 9.75
    timed = frame(lap_dist_pct=0.5, session_laps_remain=-1, session_time_remain=600.0)
    assert race_laps_remaining(timed, RACE, 92.0) == 0.5 + 7 + 1
    assert race_laps_remaining(timed, RACE, None) is None
    assert race_laps_remaining(f, SessionInfo(session_type="Practice"), 92.0) is None
    unlimited = frame(session_laps_remain=-1, session_time_remain=604800.0)
    assert race_laps_remaining(unlimited, RACE, 92.0) is None


# --- fenêtre de stand
def fuel_status(fuel: float, remaining: float, avg: float = 2.5) -> object:
    m = FuelModel(window=5, margin_laps=1.0, reserve_l=0.3)
    m.add_lap(lap(1, avg))
    return m.status(fuel, remaining)


def test_pit_window_single_stop() -> None:
    w = pit_window(fuel_status(26.0, 12.0), 100.0, 1, 0.0, 1.0)  # type: ignore[arg-type]
    assert w.advice == "open" and w.stops_needed == 1 and w.is_open
    assert w.open_lap == 1 and w.close_lap == 10
    assert w.fuel_to_add_l == pytest.approx(6.8)


def test_pit_window_small_tank_multi_stop() -> None:
    w = pit_window(fuel_status(20.0, 30.0), 20.0, 1, 0.0, 1.0)  # type: ignore[arg-type]
    assert w.stops_needed == 3 and w.advice == "wait" and not w.is_open
    assert (w.open_lap, w.close_lap) == (7, 7)
    assert w.fuel_to_add_l == 0.0  # réservoir plein pour l'instant
    later = pit_window(fuel_status(2.6, 23.1), 20.0, 7, 0.9, 1.0)  # type: ignore[arg-type]
    assert later.advice == "last_chance" and later.fuel_to_add_l == pytest.approx(17.4)


def test_pit_window_no_stop_unknown_and_long_yellow() -> None:
    assert pit_window(fuel_status(60.0, 12.0), 100.0, 1, 0.0, 1.0).advice == "no_stop"  # type: ignore[arg-type]
    unknown = FuelModel().status(20.0, 12.0)
    assert pit_window(unknown, 100.0, 1, 0.0, 1.0).advice == "unknown"
    st = fuel_status(26.0, 12.0)
    assert pit_window(st, 100.0, 1, 0.0, 1.0, long_yellow=True).advice == "pit_now_yellow"  # type: ignore[arg-type]
    closed = fuel_status(20.0, 30.0)
    assert pit_window(closed, 20.0, 1, 0.0, 1.0, long_yellow=True).advice == "wait"  # type: ignore[arg-type]


# --- pneus, météo, écarts
def slow_wear(w: float) -> dict[str, float]:
    return {f"tyre_{c}_wear_{p}": w for c in ("lf", "rf", "lr", "rr") for p in "lmr"}


def test_tyre_wear_extrapolation_and_new_tyres() -> None:
    t = TyreModel(wear_warning_pct=40.0)
    t.on_slow({**slow_wear(1.0), "tyre_lf_temp_cl": 80.0, "tyre_lf_temp_cm": 78.0,
               "tyre_lf_temp_cr": 75.0, "tyre_lf_pressure": 175.0})  # fmt: skip
    for _ in range(10):
        t.on_lap(False)
    assert t.status().stint_laps == 10 and t.status().wear_per_lap_pct is None
    t.on_slow(slow_wear(0.80))  # lecture au stand : 2 % par tour
    st = t.status()
    assert st.wear_per_lap_pct == pytest.approx(2.0) and st.wear_pct == pytest.approx(80.0)
    for _ in range(5):
        t.on_lap(False)
    st = t.status()
    assert st.wear_pct == pytest.approx(70.0) and st.laps_to_threshold == pytest.approx(15.0)
    assert st.temps_c["lf"] == (80.0, 78.0, 75.0) and st.pressures_kpa["lf"] == 175.0
    t.on_slow(slow_wear(1.0))  # pneus neufs
    assert t.status().stint_laps == 0 and t.status().wear_pct == 100.0


def test_weather_messages() -> None:
    w = WeatherMonitor(temp_delta_c=3.0)
    assert w.on_slow({"track_temp": 30.0, "air_temp": 20.0, "track_wetness": 1.0}) == []
    assert w.on_slow({"track_temp": 32.0, "track_wetness": 1.0}) == []
    assert w.on_slow({"track_temp": 33.2, "track_wetness": 1.0}) == [
        ("weather_track_temp", {"temp": 33, "delta": 3})
    ]
    assert w.on_slow({"track_temp": 33.5, "track_wetness": 3.0}) == [("weather_wet", {})]
    assert w.on_slow({"track_temp": 33.5, "track_wetness": 2.0}) == [("weather_drying", {})]
    assert w.status().track_temp_delta_c == pytest.approx(3.5)


def test_gaps() -> None:
    pcts = np.array([0.50, 0.52, 0.45, 0.90, -1.0])
    surf = np.array([3, 3, 3, 1, -1])
    f = frame(lap_dist_pct=0.50, car_idx_lap_dist_pct=pcts, car_idx_track_surface=surf)
    g = compute_gaps(f, 100.0)
    assert g.ahead_idx == 1 and g.ahead_s == pytest.approx(2.0)
    assert g.behind_idx == 2 and g.behind_s == pytest.approx(5.0)
    assert compute_gaps(f, None).ahead_s is None


# --- commandes de stand (règle 1)
def test_pit_commands_disabled_by_default() -> None:
    assert EngineerConfig().pit_commands_enabled is False
    cmd = make_pit_commander(EngineerConfig().pit_commands_enabled, object())
    assert isinstance(cmd, DisabledPitCommander)
    eng = RaceEngineer(EngineerConfig(), RACE)
    assert eng.set_pit_fuel(20.0) == "Commandes de stand désactivées dans la configuration"
    rec = make_pit_commander(True, object())
    assert isinstance(rec, RecordingPitCommander)
    eng = RaceEngineer(EngineerConfig(), RACE, commander=rec)
    assert eng.set_pit_fuel(17.2) == "Stand réglé : 18 litres"
    assert eng.set_pit_tyres(False) == "Stand réglé : pas de pneus"
    assert rec.commands == [("fuel", 18.0), ("tyres", 0.0)]


# --- intégration
def run_engineer(sc: Scenario) -> tuple[RaceEngineer, list[tuple[int, Utterance]]]:
    src = SyntheticSource(sc)
    info = src.session_info()
    eng = RaceEngineer(EngineerConfig(), info)
    seg = LapSegmenter(info.track_length_m)
    said: list[tuple[int, Utterance]] = []
    for f in src.frames():
        done = seg.on_frame(f)
        said += [(f.lap, u) for u in eng.on_frame(f)]
        if done is not None:
            said += [(f.lap, u) for u in eng.on_lap(done)]
    return eng, said


def test_race_scenario_calls() -> None:
    eng, said = run_engineer(get_scenario("race"))
    keys = [(lap_no, u.key) for lap_no, u in said]
    assert (2, "fuel_first") in keys and (2, "pit_open") in keys
    first = next(u for _, u in said if u.key == "fuel_first")
    assert first.text == "Carburant pour 9,3 tours, il en reste 11,0"
    opened = next(u for _, u in said if u.key == "pit_open")
    assert opened.text == "Fenêtre de stand ouverte, ajoute 7 litres"
    assert [k for _, k in keys].count("pit_open") == 1  # annoncé une seule fois
    last_chance = [lap_no for lap_no, k in keys if k == "pit_last_chance"]
    assert last_chance and last_chance[0] == 10  # « stand ce tour » dit au début du tour 10
    assert any(k == "weather_track_temp" for _, k in keys)
    st = eng.state
    assert st.best_lap_time is not None and st.best_lap_time < LAP_TIME + 0.5
    assert eng.fuel.clean_laps == 5  # la panne sèche finale du scénario fausse la moyenne
    assert st.gaps.ahead_s is not None and st.laps_completed == 11


def test_long_full_course_yellow_suggests_pit_once() -> None:
    sc = Scenario(
        n_laps=6, session_type="Race", race_laps=14, fuel_start_l=26.0,
        flag_periods=((2.2 * LAP_TIME, 3.0 * LAP_TIME, int(Flags.CAUTION_WAVING)),),
    )  # fmt: skip
    _, said = run_engineer(sc)
    calls = [u for _, u in said if u.key == "pit_now_yellow"]
    assert len(calls) == 1
    assert calls[0].text.startswith("Jaune : stand maintenant conseillé, ajoute ")


def test_practice_low_fuel_and_lap_times() -> None:
    _, said = run_engineer(Scenario(n_laps=5, fuel_start_l=14.0))
    keys = [u.key for _, u in said]
    assert keys.count("fuel_first") == 1 and "fuel_low" in keys
    assert next(u for _, u in said if u.key == "lap_time").text.startswith("Meilleur tour, 1:32")
    assert not math.isnan(LAP_TIME)
