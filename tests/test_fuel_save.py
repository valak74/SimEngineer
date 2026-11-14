from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from simengineer.app import SimEngineerApp
from simengineer.coach.corners import detect_corners
from simengineer.core.config import AppConfig, CoachConfig
from simengineer.engineer.fuel import FuelStatus
from simengineer.engineer.fuel_save import (
    full_throttle_rate,
    lift_curve,
    plan_fuel_save,
)
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    LapStyle,
    Scenario,
    SyntheticSource,
    build_lap_profile,
    lap_style,
)
from simengineer.telemetry.laps import LapSegmenter
from simengineer.telemetry.resample import GridLap, resample_lap
from simengineer.voice.speaker import NullSpeaker

L = DEFAULT_TRACK.length_m


def grid_of(style: LapStyle = ()) -> GridLap:
    src = SyntheticSource(Scenario(n_laps=3, lap_styles=((1, style),), fuel_throttle_model=True))
    seg = LapSegmenter(L)
    laps = [lap for f in src.frames() if (lap := seg.on_frame(f)) is not None]
    return resample_lap(laps[1])


def status(fuel: float, remaining: float, avg: float = 2.5) -> FuelStatus:
    needed = (remaining + 1.0) * avg + 0.3
    return FuelStatus(
        fuel_l=fuel, clean_laps=3, avg_per_lap_l=avg, laps_in_tank=(fuel - 0.3) / avg,
        laps_remaining=remaining, fuel_needed_l=needed, fuel_to_add_l=max(needed - fuel, 0.0),
        margin_laps=(fuel - 0.3) / avg - remaining, can_finish=needed <= fuel,
    )  # fmt: skip


def test_lift_estimate_matches_synthetic_model() -> None:
    """L'estimation d'économie et de temps perdu est du bon ordre (modèle synthétique)."""
    base = grid_of()
    corners = detect_corners(base, CoachConfig())
    rate = full_throttle_rate(base, 2.5)
    curve = dict((d, (s, c)) for d, s, c in lift_curve(base, corners[2], corners[1].end_m, rate))
    save, cost = curve[80.0]
    lifted = build_lap_profile(DEFAULT_TRACK, lap_style(c2=CornerStyle(lift_m=80.0)))
    ideal = build_lap_profile(DEFAULT_TRACK)
    w = lambda p: float((0.08 + 0.92 * p.throttle).sum())  # noqa: E731
    true_save = 2.5 * (1 - w(lifted) / w(ideal))
    true_cost = lifted.lap_time - ideal.lap_time
    assert save == pytest.approx(true_save, rel=0.35)
    assert true_cost * 0.5 <= cost <= true_cost * 3.0  # estimation prudente


def test_plan_targets_and_limits() -> None:
    base = grid_of()
    corners = detect_corners(base, CoachConfig())
    plan = plan_fuel_save(base, corners, status(fuel=26.5, remaining=11.0))
    assert plan is not None and plan.feasible
    need = (status(26.5, 11.0).fuel_to_add_l - 2.5 + 0.5) / 11.0  # type: ignore[operator]
    assert plan.target_per_lap_l == pytest.approx(2.5 - need)
    assert plan.save_per_lap_l >= need and len(plan.lifts) <= 4
    assert all(10.0 <= x.lift_m <= 150.0 for x in plan.lifts)
    assert plan.cost_per_lap_s < 1.0
    # Rien à économiser / trop d'écart (il faut s'arrêter) / pas de données
    assert plan_fuel_save(base, corners, status(fuel=40.0, remaining=11.0)) is None
    assert plan_fuel_save(base, corners, status(fuel=15.0, remaining=11.0)) is None
    assert plan_fuel_save(base, [], status(fuel=26.5, remaining=11.0)) is None
    assert plan_fuel_save(base, corners, FuelStatus(26.5, 0)) is None


def run(cfg: AppConfig, styles: tuple) -> tuple[SimEngineerApp, list[tuple[str, str]]]:  # type: ignore[type-arg]
    shutil.rmtree(cfg.data_dir, ignore_errors=True)
    cfg.source.realtime = False
    sc = Scenario(
        n_laps=12, session_type="Race", race_laps=12, fuel_start_l=29.0, fuel_per_lap_l=2.5,
        fuel_throttle_model=True, lap_styles=styles,
    )  # fmt: skip
    speaker = NullSpeaker()
    app = SimEngineerApp(cfg, SyntheticSource(sc), speaker)
    app.run()
    return app, [(u.key, u.text) for u in speaker.said if u.key.startswith(("fuel", "pit"))]


PLAN1 = lap_style(c0=CornerStyle(lift_m=70), c2=CornerStyle(lift_m=50),
                  c3=CornerStyle(lift_m=50), c4=CornerStyle(lift_m=60))  # fmt: skip
PLAN2 = lap_style(c0=CornerStyle(lift_m=100), c2=CornerStyle(lift_m=70),
                  c3=CornerStyle(lift_m=70), c4=CornerStyle(lift_m=80))  # fmt: skip


def test_driver_following_the_plan_finishes_without_stopping(cfg: AppConfig) -> None:
    styles = tuple((i, PLAN1) for i in range(2, 5)) + tuple((i, PLAN2) for i in range(5, 12))
    app, said = run(cfg, styles)
    keys = [k for k, _ in said]
    first = next(t for k, t in said if k == "fuel_save_plan")
    assert first.startswith("Économie de carburant : 0,16 litre par tour. Lève le pied 70 mètres")
    assert "pit_open" not in keys and "pit_last_chance" not in keys  # pas d'appel au stand
    assert keys.count("fuel_save_plan") >= 2  # renforcé quand l'économie ne suffit pas
    assert said[-1] == ("fuel_save_check", "Conso 2,32, objectif tenu")
    assert app.engineer is not None and app.engineer.state.fuel.fuel_l > 0.5  # arrivé


def test_driver_ignoring_the_plan_is_sent_to_the_pits(cfg: AppConfig) -> None:
    _, said = run(cfg, ())
    keys = [k for k, _ in said]
    assert "fuel_save_plan" in keys and "fuel_save_abort" in keys
    abort = keys.index("fuel_save_abort")
    assert said[abort][1].startswith("Économie insuffisante, prévois un arrêt : ")
    assert "pit_open" in keys[abort:]  # retour à la stratégie d'arrêt


def test_no_fuel_saving_outside_races(cfg: AppConfig, tmp_path: Path) -> None:
    cfg.source.realtime = False
    sc = Scenario(n_laps=5, fuel_start_l=12.0, fuel_throttle_model=True)
    speaker = NullSpeaker()
    SimEngineerApp(cfg, SyntheticSource(sc), speaker).run()
    assert not any(u.key.startswith("fuel_save") for u in speaker.said)
