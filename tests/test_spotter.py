from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from simengineer.core.config import SpotterConfig
from simengineer.core.vars import CarLeftRight, Flags, TrackSurface
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import DEFAULT_TRACK, OtherCar, Scenario, SyntheticSource
from simengineer.spotter import rules
from simengineer.spotter.spotter import Spotter
from simengineer.voice.arbiter import Priority, SpeechArbiter
from simengineer.voice.speaker import NullSpeaker

L = DEFAULT_TRACK.length_m


def run(sc: Scenario, log: Path | None = None) -> list[tuple[float, str, str]]:
    """Rejoue un scénario à travers spotter + arbitre ; renvoie (temps, clé, texte) dits."""
    src = SyntheticSource(sc)
    spotter = Spotter(SpotterConfig(), L, "fr", log)
    now = [0.0]
    speaker = NullSpeaker()
    arbiter = SpeechArbiter(speaker, lambda: now[0])
    said: list[tuple[float, str, str]] = []
    for f in src.frames():
        now[0] = f.session_time
        for utt in spotter.on_frame(f, time.perf_counter()):
            if arbiter.submit(utt):
                pass
        arbiter.tick()
        while len(said) < len(speaker.said):
            utt = speaker.said[len(said)]
            said.append((f.session_time, utt.key, utt.text))
    spotter.close()
    return said


# --- règles pures
def test_side_keys() -> None:
    assert rules.side_key(int(CarLeftRight.CAR_LEFT)) == "car_left"
    assert rules.side_key(int(CarLeftRight.CLEAR)) is None
    assert rules.still_key(int(CarLeftRight.TWO_CARS_RIGHT)) == "still_right"
    assert rules.still_key(int(CarLeftRight.CAR_LEFT_RIGHT)) is None


def test_speed_estimation_wraps_finish_line() -> None:
    old, new = np.array([0.999, 0.5, 0.2]), np.array([0.001, 0.5, 0.21])
    v = rules.estimate_speeds(old, new, 0.25, 4000.0)
    assert np.allclose(v, [32.0, 0.0, 160.0])


def test_slow_cars_ahead_filters() -> None:
    pcts = np.array([0.10, 0.15, 0.05, 0.16, 0.17, -1.0])
    speeds = np.array([50.0, 0.0, 0.0, 0.0, 40.0, 0.0])
    surf = np.array([3, 3, 3, 0, 3, -1])
    pit = np.array([False, False, False, False, False, False])
    hz = rules.slow_cars_ahead(0, 0.10, pcts, speeds, surf, pit, 4000.0, 400.0, 8.0)
    assert [(h.car_idx, round(h.distance_m), h.offtrack) for h in hz] == [
        (1, 200, False),
        (3, 240, True),
    ]  # derrière (2), rapide (4) et absente (5) ignorées
    pit[1] = True
    hz = rules.slow_cars_ahead(0, 0.10, pcts, speeds, surf, pit, 4000.0, 400.0, 8.0)
    assert [h.car_idx for h in hz] == [3]


def test_announce_distance() -> None:
    assert rules.announce_distance(312) == 300
    assert rules.announce_distance(20) == 50


def test_flag_calls() -> None:
    y, g = int(Flags.YELLOW_WAVING), int(Flags.GREEN)
    assert rules.flag_calls(0, y) == [("flag_yellow", Priority.P0)]
    assert rules.flag_calls(y, y | int(Flags.YELLOW)) == []
    assert rules.flag_calls(y, g) == [("flag_green", Priority.P1)]
    assert rules.flag_calls(0, int(Flags.CAUTION_WAVING)) == [("flag_fcy", Priority.P0)]
    assert ("flag_blue", Priority.P1) in rules.flag_calls(0, int(Flags.BLUE))
    assert rules.flag_calls(0, int(Flags.RED))[0] == ("flag_red", Priority.P0)


# --- scénarios
def test_stopped_car_300m(tmp_path: Path) -> None:
    log = tmp_path / "alerts.jsonl"
    said = run(get_scenario("stopped_car"), log)
    stops = [s for s in said if s[1].startswith("stopped:5") and s[0] < 60.0]  # 1er tour
    assert len(stops) == 2, said
    t_first, key, text = stops[0]
    assert key == "stopped:5:far"
    assert 10.0 < t_first < 10.8  # détection en moins d'une seconde après l'arrêt
    assert text in ("Voiture arrêtée devant, 300 mètres", "Voiture arrêtée devant, 250 mètres")
    assert stops[1][1] == "stopped:5:near" and stops[1][2].endswith("150 mètres")
    assert "gauche" not in text and "droite" not in text  # jamais de côté annoncé
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in records][:2] == ["stopped_ahead", "stopped_ahead"]
    assert all(r["priority"] == 0 and r["processing_ms"] < 200.0 for r in records)


def test_overtake_side_calls() -> None:
    said = run(get_scenario("overtake"))
    keys = [s[1] for s in said]
    assert keys[0] == "car_right" and keys[-1] == "clear"
    assert set(keys) <= {"car_right", "still_right", "clear"}
    assert keys.count("car_right") == 1 and keys.count("clear") == 1


def test_accident_scenario() -> None:
    said = run(get_scenario("accident"))
    keys = [s[1] for s in said]
    assert "flag_yellow" in keys and "accident" in keys
    acc = next(s for s in said if s[1] == "accident")
    assert acc[2].startswith("Accident devant, ")
    assert keys.index("flag_yellow") < keys.index("accident")
    first_lap = [s[1] for s in said if s[0] < 90.0]
    assert first_lap.count("accident") == 1
    assert sum(k.startswith("stopped:") for k in first_lap) == 1  # un seul rappel de près
    assert "flag_green" in keys  # fin du jaune


def test_no_false_alarm_on_clean_laps_and_pits() -> None:
    assert run(Scenario(n_laps=2)) == []
    parked = Scenario(n_laps=2, others=(OtherCar(idx=8, gap_m=500.0, pace=0.0, in_pits=True),))
    assert run(parked) == []


def test_slow_player_under_caution_gets_no_stopped_alert() -> None:
    src = SyntheticSource(get_scenario("stopped_car"))
    spotter = Spotter(SpotterConfig(), L)
    out = []
    for f in src.frames():
        f.speed = 10.0
        out += [u for u in spotter.on_frame(f) if u.key.startswith(("stopped", "accident"))]
    assert out == []


def test_offtrack_self_and_hysteresis() -> None:
    said = run(Scenario(n_laps=2, offtracks=((0, 0.30, 0.32),)))
    assert [s[1] for s in said] == ["offtrack_self"]
    # Un état « voiture à gauche » d'une seule image ne déclenche rien.
    src = SyntheticSource(Scenario(n_laps=1))
    spotter = Spotter(SpotterConfig(), L)
    out = []
    for i, f in enumerate(src.frames()):
        if i == 500:
            f.car_left_right = int(CarLeftRight.CAR_LEFT)
        out += spotter.on_frame(f)
        if i > 600:
            break
    assert out == []


def test_in_battle_context() -> None:
    src = SyntheticSource(get_scenario("overtake"))
    spotter = Spotter(SpotterConfig(), L)
    flags = []
    for f in src.frames():
        spotter.on_frame(f)
        flags.append(spotter.in_battle)
    assert not flags[0] and any(flags) and not flags[-1]
    assert TrackSurface.ON_TRACK == 3


def test_player_trail_time_gaps() -> None:
    trail = rules.PlayerTrail(1000.0, step_m=5.0)
    for i in range(0, 201):  # 10 m/s pendant 20 s : un tour de 200 m... sur 1000 m
        trail.update((i * 0.5) / 100.0 % 1.0, i * 0.5)
    pcts = np.array([0.95, 0.90, 0.99, -1.0])
    gaps = trail.gaps(pcts, 100.0)
    assert gaps[0] == pytest.approx(5.0, abs=0.6) and gaps[1] == pytest.approx(10.0, abs=0.6)
    assert np.isnan(gaps[3])


def test_faster_car_rule() -> None:
    now = np.array([0.0, 2.0, 2.0, 2.0])
    before = np.array([0.0, 2.2, 2.0, 2.5])  # voiture 1 : +0,1 s/s ; 2 : même rythme ; 3 : saut
    gap_m = np.array([0.0, 80.0, 80.0, 80.0])
    surf = np.array([3, 3, 3, 3])
    out = rules.faster_cars_behind(0, now, before, gap_m, 2.0, surf, None, 3.0, 400.0, 0.04)
    assert [(c.car_idx, round(c.rate, 2)) for c in out] == [(1, 0.1), (3, 0.25)]
    beside = rules.faster_cars_behind(0, now, before, gap_m * 0 + 5, 2.0, surf, None, 3, 400, 0.04)
    assert beside == []  # à hauteur : CarLeftRight s'en charge


def test_multiclass_faster_car_announced_once() -> None:
    said = run(get_scenario("multiclass"))
    faster = [(t, k, x) for t, k, x in said if k.startswith("faster")]
    assert [(k, x) for _, k, x in faster] == [
        ("faster:11", "Voiture plus rapide, 3 secondes derrière")
    ]
    t_alert = faster[0][0]
    t_left = next(t for t, k, _ in said if k == "car_left")
    assert 1.0 < t_left - t_alert < 30.0  # annoncée bien avant d'arriver à hauteur
    for name in ("race", "overtake", "clean", "perf"):  # même rythme : jamais d'alerte
        assert not any(k.startswith("faster") for _, k, _ in run(get_scenario(name)))
