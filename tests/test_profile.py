from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.coach.calibration import apply_calibration, calibrate, measure_dispersion
from simengineer.coach.profile import DriverProfile, build_profile, render_profile
from simengineer.coach.training import build_plan
from simengineer.core.config import AppConfig, CoachConfig
from simengineer.reference.importer import import_ibt
from simengineer.sources.ibt_format import write_ibt
from simengineer.sources.synthetic import (
    CornerStyle,
    LapStyle,
    Scenario,
    SyntheticSource,
    generate,
    lap_style,
)
from simengineer.store.db import Store

TRACK, CAR = "Synthetic Ring", "Porsche 911 GT3 Cup (992)"


def run_session(cfg: AppConfig, store: Store, sc: Scenario) -> SimEngineerApp:
    cfg.source.realtime = False
    app = SimEngineerApp(cfg, SyntheticSource(sc), store=store)
    app.run()
    return app


def with_reference(cfg: AppConfig, tmp_path: Path) -> Store:
    store = Store(cfg.data_dir)
    ibt = tmp_path / "ref.ibt"
    write_ibt(generate(Scenario(n_laps=3)), ibt)
    import_ibt(store, ibt, "mon tour", "personnel")
    return store


def style(brake: float) -> LapStyle:
    return lap_style(c2=CornerStyle(brake_offset_m=brake), c5=CornerStyle(throttle_offset_m=14.0))


@pytest.fixture
def three_sessions(cfg: AppConfig, tmp_path: Path) -> Store:
    """Trois séances : le freinage du T03 progresse, la reprise des gaz du T06 stagne."""
    store = with_reference(cfg, tmp_path)
    for brake in (-22.0, -15.0, -8.0):
        run_session(cfg, store, Scenario(n_laps=4, base_style=style(brake)))
    return store


def test_profile_trends_per_corner(three_sessions: Store) -> None:
    p = build_profile(three_sessions, TRACK, CAR)
    assert p is not None and p.sessions == 3 and p.laps_valid == 9
    by = {c.corner: c for c in p.corners}
    t3, t6 = by["T03"], by["T06"]
    assert t3.status == "improving" and t3.trend_s_per_session < -0.1
    assert t3.main_issue == "brake_later" and t3.issue_share == 1.0
    assert t3.last_loss_s < t3.mean_loss_s
    assert t6.status == "stable" and t6.main_issue == "throttle_earlier"
    assert by["T01"].status == "stable" and abs(by["T01"].mean_loss_s) < 0.01
    assert [c.corner for c in p.weaknesses] == ["T03", "T06"]
    assert p.main_theme == "braking"
    assert set(p.theme_cost_s) == {"braking", "throttle"}
    text = render_profile(p, "fr")
    assert "freinage trop tôt (100 %) — en progrès" in text and "| T06 |" in text
    assert "Driver profile" in render_profile(p, "en")
    assert build_profile(three_sessions, "Autre circuit", CAR) is None


def test_training_plan_one_theme(three_sessions: Store) -> None:
    p = build_profile(three_sessions, TRACK, CAR)
    assert p is not None
    plan = build_plan(p, "fr")
    assert plan.theme == "braking" and plan.title == "Freinage"
    assert plan.focus_corners == ["T03"] and len(plan.drills) == 3
    assert plan.target.startswith("Perte moyenne au T03 : passer de 0,")
    text = plan.render("fr")
    assert "# Programme de la séance : Freinage" in text and "## Objectif mesurable" in text
    assert build_plan(p, "fr", avoid="braking").theme == "braking"  # l'autre thème pèse trop peu
    p.theme_cost_s["throttle"] = p.theme_cost_s["braking"] * 0.9
    assert build_plan(p, "fr", avoid="braking").theme == "throttle"  # alternance
    assert build_plan(p, "en").title == "Braking"


def test_consistency_theme_when_nothing_else() -> None:
    p = DriverProfile(TRACK, CAR, 1, 8, 92.0, 0.9, [], {"consistency": 0.9})
    plan = build_plan(p, "fr")
    assert plan.theme == "consistency" and "sous 0,45 s" in plan.target
    empty = DriverProfile(TRACK, CAR, 1, 2, 92.0, None)
    assert build_plan(empty, "fr").theme == "consistency"


def test_calibration_follows_driver_dispersion(cfg: AppConfig, tmp_path: Path) -> None:
    base = CoachConfig()
    store = with_reference(cfg, tmp_path)
    # Pilote régulier : mêmes repères à chaque tour -> seuils de base conservés.
    run_session(cfg, store, Scenario(n_laps=6, base_style=style(-10.0)))
    assert measure_dispersion(store, TRACK, CAR)["brake_point_m"] < 1.0
    assert calibrate(store, TRACK, CAR, base)["brake_point_m"] == base.brake_point_m
    assert apply_calibration(store, TRACK, CAR, base) == base

    # Pilote irrégulier : repères de freinage qui bougent de ±10 m partout.
    cfg2 = cfg.model_copy(deep=True)
    cfg2.data_dir = tmp_path / "irregulier"
    store2 = with_reference(cfg2, tmp_path)
    offsets = (-2.0, -22.0, -6.0, -26.0, -12.0, -18.0)
    laps = tuple(
        (i, tuple((c, CornerStyle(brake_offset_m=o)) for c in range(8)))
        for i, o in enumerate(offsets)
    )
    app = run_session(cfg2, store2, Scenario(n_laps=7, lap_styles=laps))
    sigma = measure_dispersion(store2, TRACK, CAR)["brake_point_m"]
    assert 6.0 < sigma < 12.0
    stored = store2.kv_get(f"thresholds:{TRACK}:{CAR}")  # calibré en fin de séance
    assert stored["brake_point_m"] == pytest.approx(1.5 * sigma, abs=0.01)
    tuned = apply_calibration(store2, TRACK, CAR, base)
    assert tuned.brake_point_m > base.brake_point_m and tuned.min_speed_kmh == base.min_speed_kmh
    assert app.coach_cfg == base  # la séance en cours garde les seuils du démarrage
    nxt = run_session(cfg2, store2, Scenario(n_laps=3))
    assert nxt.coach_cfg.brake_point_m == tuned.brake_point_m  # appliqué à la séance suivante


def test_calibration_is_capped(cfg: AppConfig) -> None:
    store = Store(cfg.data_dir)
    store.kv_set(f"thresholds:{TRACK}:{CAR}", {"brake_point_m": 12.0, "inconnu": 5.0})
    tuned = apply_calibration(store, TRACK, CAR, CoachConfig())
    assert tuned.brake_point_m == 12.0 and not hasattr(tuned, "inconnu")


def test_cli_profile_and_training(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMENGINEER__LOGGING__LEVEL", "WARNING")
    runner = CliRunner()
    assert runner.invoke(cli_app, ["profile"]).exit_code == 1
    args = ["run", "-s", "synthetic", "--scenario", "race", "--no-realtime", "--null-voice"]
    assert runner.invoke(cli_app, args).exit_code == 0
    res = runner.invoke(cli_app, ["profile"])
    assert res.exit_code == 0 and "# Profil pilote — Synthetic Ring" in res.output
    res = runner.invoke(cli_app, ["training"])
    assert res.exit_code == 0 and "# Programme de la séance" in res.output
    assert "Synthetic Ring" in runner.invoke(cli_app, ["sessions"]).output
