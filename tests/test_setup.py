from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.coach.corners import Corner, detect_corners
from simengineer.core.config import AppConfig, CoachConfig, SetupConfig
from simengineer.setup.compare import (
    add_mark,
    compare_mark,
    compare_stints,
    list_marks,
    render_comparison,
)
from simengineer.setup.diagnosis import (
    Finding,
    _phase_masks,
    diagnose,
    lap_balance,
    tyre_findings,
)
from simengineer.setup.guide import apply_guide, describe, render_diagnosis, suggestions
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    Scenario,
    SyntheticSource,
    lap_style,
)
from simengineer.store.db import Store
from simengineer.telemetry.laps import LapSegmenter
from simengineer.telemetry.resample import GridLap, resample_lap

CFG = SetupConfig()


def clean_grids(n: int = 3, sc: Scenario | None = None) -> list[GridLap]:
    src = SyntheticSource(sc or Scenario(n_laps=n + 1))
    seg = LapSegmenter(DEFAULT_TRACK.length_m)
    return [resample_lap(lap) for f in src.frames() if (lap := seg.on_frame(f)) is not None]


@pytest.fixture(scope="module")
def base() -> tuple[list[GridLap], list[Corner]]:
    grids = clean_grids()
    return grids, detect_corners(grids[0], CoachConfig())


def with_steering(g: GridLap, fn: object, phase: str, corners: list[Corner]) -> GridLap:
    mask = _phase_masks(g.n, g.step, corners)[phase]
    channels = dict(g.channels)
    channels["steering"] = fn(g.ch("steering").copy(), mask)  # type: ignore[operator]
    return GridLap(g.track_length_m, g.lap_time, channels, g.step, dict(g.meta))


def test_neutral_car_has_no_finding(base: tuple[list[GridLap], list[Corner]]) -> None:
    grids, corners = base
    d = diagnose(grids, corners, CFG)
    assert d.findings == [] and d.laps == 3
    for phase in ("entry", "mid", "exit"):
        assert d.balance[phase].understeer_index == pytest.approx(1.0, abs=0.05)
        assert d.balance[phase].oversteer_events == 0.0
    assert "Aucun déséquilibre net" in render_diagnosis(d, "fr")


def test_understeer_detected_in_the_right_phase(base: tuple[list[GridLap], list[Corner]]) -> None:
    grids, corners = base

    def more_lock(steer: np.ndarray, mask: np.ndarray) -> np.ndarray:  # type: ignore[type-arg]
        steer[mask] *= 1.9
        return steer

    laps = [with_steering(g, more_lock, "mid", corners) for g in grids]
    d = diagnose(laps, corners, CFG)
    assert [f.key for f in d.findings] == ["understeer:mid"]
    assert d.findings[0].value == pytest.approx(1.9, abs=0.1)
    text = render_diagnosis(d, "fr")
    assert "## Sous-virage à mi-virage (indice 1,9" in text
    assert "Barre antiroulis avant : un cran plus souple" in text
    assert "les setups iRacing sont chiffrés" in text
    assert "Understeer mid-corner" in render_diagnosis(d, "en")


def test_oversteer_detected_from_counter_steer(base: tuple[list[GridLap], list[Corner]]) -> None:
    grids, corners = base

    def snap(steer: np.ndarray, mask: np.ndarray) -> np.ndarray:  # type: ignore[type-arg]
        idx = np.flatnonzero(mask)
        starts = idx[np.flatnonzero(np.diff(np.concatenate(([-9], idx))) > 1)]
        for s in starts[:4]:  # contre-braquage de 6 m à la sortie de 4 virages
            steer[s + 8 : s + 14] *= -0.5
        return steer

    laps = [with_steering(g, snap, "exit", corners) for g in grids]
    d = diagnose(laps, corners, CFG)
    assert [f.key for f in d.findings] == ["oversteer:exit"]
    assert d.findings[0].value == 4.0
    assert "Survirage en sortie (4,0 contre-braquages par tour)" in render_diagnosis(d, "fr")


def test_sign_convention_is_not_assumed(base: tuple[list[GridLap], list[Corner]]) -> None:
    grids, corners = base
    g = grids[0]
    flipped = GridLap(g.track_length_m, g.lap_time, {**g.channels, "steering": -g.ch("steering")})
    bal = lap_balance(flipped, corners, CFG)
    assert bal["mid"].oversteer_events == 0.0
    assert bal["mid"].understeer_index == pytest.approx(1.0, abs=0.05)
    no_lat = GridLap(g.track_length_m, g.lap_time, {"time": g.ch("time"), "speed": g.ch("speed")})
    assert lap_balance(no_lat, corners, CFG) == {}


def test_tyre_findings() -> None:
    temps = {
        "lf": (70.0, 80.0, 90.0),  # intérieur (droite) bien plus chaud : trop de carrossage
        "rf": (82.0, 81.0, 80.0),  # intérieur (gauche) à peine plus chaud : pas assez
        "lr": (76.0, 88.0, 82.0),  # milieu chaud : pression trop haute
        "rr": (84.0, 74.0, 78.0),  # milieu froid : pression trop basse
    }
    keys = {f.key: f for f in tyre_findings(temps, CFG)}
    assert set(keys) == {"camber_high:lf", "camber_low:rf", "pressure_high:lr", "pressure_low:rr"}
    assert keys["camber_high:lf"].value == 20.0
    hot_front = {k: (95.0, 96.0, 100.0) if k[1] == "f" else (80.0, 81.0, 85.0) for k in temps}
    hot_front["rf"], hot_front["rr"] = (100.0, 96.0, 95.0), (85.0, 81.0, 80.0)
    assert [f.key for f in tyre_findings(hot_front, CFG)] == ["axle_hot:front"]
    assert tyre_findings({"lf": (78.0, 80.0, 84.0)}, CFG) == []


def test_guide_texts() -> None:
    f = Finding("camber_high", "lf", 20.0)
    assert describe(f, "fr") == "Trop de carrossage — avant gauche (20,0 °C)"
    assert suggestions(f, "fr") == ["Avant gauche : réduis le carrossage négatif de 0,2°."]
    assert len(suggestions(Finding("oversteer", "entry", 2.0), "en")) == 3
    assert "UN SEUL réglage" in apply_guide("fr") and "ONE setting" in apply_guide("en")


def test_before_after_verdicts(base: tuple[list[GridLap], list[Corner]]) -> None:
    grids, corners = base
    slow = clean_grids(
        3, Scenario(n_laps=4, base_style=lap_style(c3=CornerStyle(min_speed_delta=-1.5)))
    )
    better = compare_stints(slow, grids, corners, CFG, "ARB avant -1")
    assert better.verdict == "better" and better.mean_delta_s == pytest.approx(-0.15, abs=0.03)
    assert compare_stints(grids, slow, corners, CFG).verdict == "worse"
    assert compare_stints(grids, grids, corners, CFG).verdict == "inconclusive"
    assert compare_stints(grids[:2], grids, corners, CFG).verdict == "not_enough_laps"
    text = render_comparison(better, "fr")
    assert "# Avant / après : ARB avant -1" in text and "MIEUX — garde ce réglage" in text
    assert "BETTER" in render_comparison(better, "en")


def test_marks_split_session_on_clean_laps(cfg: AppConfig) -> None:
    cfg.source.realtime = False
    slow = lap_style(c3=CornerStyle(min_speed_delta=-1.5))
    styles = tuple((i, slow) for i in range(4))  # tours 1 à 4 lents, 5 à 9 rapides
    sc = Scenario(n_laps=10, lap_styles=styles, offtracks=((1, 0.3, 0.32),))
    store = Store(cfg.data_dir)
    sid = SimEngineerApp(cfg, SyntheticSource(sc), store=store).run()
    assert compare_mark(store, sid, cfg.setup, cfg.coach) is None
    store.db.execute(
        "INSERT INTO setup_marks (session_id, created_at, after_lap, description)"
        " VALUES (?, '2026-01-01', 4, 'aileron +1')",
        (sid,),
    )
    store.db.commit()
    res = compare_mark(store, sid, cfg.setup, cfg.coach)
    assert res is not None and res.description == "aileron +1"
    assert res.before.laps == 3  # tours 1, 3, 4 : le tour 2 (hors-piste) est exclu
    assert res.after.laps == 5 and res.verdict == "better"
    mark = add_mark(store, sid, "  pressions -3 kPa ")
    assert mark.after_lap == 10 and mark.description == "pressions -3 kPa"
    assert [m.description for m in list_marks(store, sid)] == ["aileron +1", "pressions -3 kPa"]
    last = compare_mark(store, sid, cfg.setup, cfg.coach)
    assert last is not None and last.verdict == "not_enough_laps" and last.before.laps == 5
    assert store.kv_get(f"tyres:{sid}")["lf"] == [82.0, 80.0, 78.0]


def test_cli_setup_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMENGINEER__LOGGING__LEVEL", "WARNING")
    runner = CliRunner()
    args = ["run", "-s", "synthetic", "--scenario", "clean", "--no-realtime", "--null-voice"]
    assert runner.invoke(cli_app, args).exit_code == 0
    res = runner.invoke(cli_app, ["setup", "diagnose"])
    assert res.exit_code == 0 and "# Diagnostic de setup (4 tours propres)" in res.output
    assert runner.invoke(cli_app, ["setup", "compare"]).exit_code == 1
    res = runner.invoke(cli_app, ["setup", "mark", "ARB avant -1 cran"])
    assert "Changement noté après le tour 5" in res.output
    res = runner.invoke(cli_app, ["setup", "compare"])
    assert res.exit_code == 0 and "PAS ASSEZ DE TOURS PROPRES" in res.output
    assert "Save As" in runner.invoke(cli_app, ["setup", "guide"]).output
