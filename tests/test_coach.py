from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.coach.advice import CornerDelta, advise, compare_laps, find_issues
from simengineer.coach.corners import Corner, detect_corners
from simengineer.coach.metrics import CornerMetrics, lap_metrics
from simengineer.coach.pipeline import (
    analyze_lap,
    debrief_utterances,
    match_grid,
    process_lap,
    reanalyze_session,
)
from simengineer.coach.report import build_session_summary, render_report
from simengineer.coach.worker import InlineWorker, ProcessWorker
from simengineer.core.config import AppConfig, CoachConfig
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    LapStyle,
    Scenario,
    SyntheticSource,
    lap_style,
)
from simengineer.store.db import Store
from simengineer.telemetry.laps import Lap, LapSegmenter
from simengineer.telemetry.resample import GridLap, resample_lap
from simengineer.voice.arbiter import Priority
from simengineer.voice.speaker import NullSpeaker
from simengineer.voice.templates import word_count

CFG = CoachConfig()


def laps_with(style: LapStyle) -> tuple[Lap, Lap]:
    """(tour de référence idéal, tour avec défauts injectés)."""
    src = SyntheticSource(Scenario(n_laps=3, lap_styles=((1, style),)))
    seg = LapSegmenter(DEFAULT_TRACK.length_m)
    laps = [lap for f in src.frames() if (lap := seg.on_frame(f)) is not None]
    return laps[0], laps[1]


def grids(style: LapStyle) -> tuple[GridLap, GridLap, list[Corner]]:
    ref_lap, lap = laps_with(style)
    ref, grid = resample_lap(ref_lap), resample_lap(lap)
    return ref, grid, detect_corners(ref, CFG)


def test_corner_detection_matches_track() -> None:
    ref, _, corners = grids(())
    assert len(corners) == len(DEFAULT_TRACK.corners)
    for c, truth in zip(corners, DEFAULT_TRACK.corners, strict=True):
        assert c.start_m < truth.apex_m < c.end_m
        assert abs(c.apex_m - truth.apex_m) < 20.0
        assert c.direction == truth.direction
    assert corners[0].zone_start_m == 0.0 and corners[-1].zone_end_m == ref.track_length_m
    assert all(a.zone_end_m == b.zone_start_m for a, b in pairwise(corners))
    assert Corner.from_dict(corners[2].to_dict()) == corners[2]


def test_metrics_on_ideal_lap() -> None:
    ref, _, corners = grids(())
    for m, truth in zip(lap_metrics(ref, corners), DEFAULT_TRACK.corners, strict=True):
        assert m.min_speed == pytest.approx(truth.min_speed, abs=0.2)
        assert m.brake_peak == pytest.approx(0.95, abs=0.02)
        assert m.brake_point_m is not None and m.brake_point_m < truth.apex_m
        assert m.throttle_pickup_m == pytest.approx(truth.apex_m + 10.0, abs=3.0)
        assert m.trail_m is not None and 15.0 < m.trail_m < 35.0
        assert m.exit_speed > m.min_speed and m.entry_speed > m.min_speed


def test_zone_losses_sum_to_lap_delta() -> None:
    ref, grid, corners = grids(lap_style(c1=CornerStyle(brake_offset_m=-20.0)))
    deltas = compare_laps(grid, ref, corners)
    assert sum(d.loss_s for d in deltas) == pytest.approx(grid.lap_time - ref.lap_time, abs=1e-6)


@pytest.mark.parametrize(
    ("corner", "style", "issue", "param", "value"),
    [
        (3, CornerStyle(brake_offset_m=-15.0), "brake_later", "d", 15),
        (5, CornerStyle(brake_offset_m=-25.0), "brake_later", "d", 25),
        (4, CornerStyle(min_speed_delta=-1.5), "min_speed", "kmh", 5),
        (6, CornerStyle(throttle_offset_m=14.0), "throttle_earlier", "d", 14),
        (1, CornerStyle(brake_peak=0.75), "brake_harder", "pct", 20),
    ],
)
def test_injected_fault_is_found_and_quantified(
    corner: int, style: CornerStyle, issue: str, param: str, value: int
) -> None:
    ref, grid, corners = grids(lap_style(**{f"c{corner - 1}": style}))
    advice = advise(compare_laps(grid, ref, corners), CFG)
    assert len(advice) == 1, advice
    a = advice[0]
    assert (a.corner, a.issue) == (corner, issue)
    assert a.params[param] == pytest.approx(value, abs=3)
    total = grid.lap_time - ref.lap_time
    # Une reprise tardive coûte aussi dans la ligne droite suivante (zone du virage d'après).
    assert 0.5 * total <= a.gain_s <= total + 0.02
    assert word_count(a.short) <= 8 and f"T{corner:02d}" in a.long


def test_advice_ranked_by_gain_and_limited() -> None:
    style = lap_style(
        c2=CornerStyle(brake_offset_m=-15.0),
        c3=CornerStyle(min_speed_delta=-1.5),
        c5=CornerStyle(throttle_offset_m=14.0),
    )
    ref, grid, corners = grids(style)
    analysis = analyze_lap(grid, ref, corners, CFG, "fr", "idéal")
    gains = [a.gain_s for a in analysis.advice]
    assert gains == sorted(gains, reverse=True) and len(gains) == 3
    assert analysis.advice[0].short == "Virage 3, freine 13 mètres plus tard"
    assert analysis.advice[0].long == "T03 : freine ~13 m plus tard (gain estimé 0,50 s)"
    utts = debrief_utterances(analysis, CFG)
    assert len(utts) == CFG.max_advice == 2
    assert all(u.priority is Priority.P2 for u in utts)
    en = analyze_lap(grid, ref, corners, CFG, "en").advice[0].short
    assert en == "Turn 3, brake 13 meters later"


def test_clean_lap_gets_no_advice() -> None:
    ref, grid, corners = grids(())
    assert advise(compare_laps(grid, ref, corners), CFG) == []


def _delta(lap: dict[str, object], ref: dict[str, object] | None = None) -> CornerDelta:
    base: dict[str, object] = dict(
        corner=1, time_s=10.0, entry_speed=60.0, min_speed=30.0, exit_speed=40.0, apex_m=500.0,
        brake_point_m=400.0, brake_peak=0.9, brake_rise_s=0.10, trail_m=30.0,
        throttle_pickup_m=520.0, steering_corrections=1,
    )  # fmt: skip
    c = Corner(1, 450.0, 500.0, 560.0, 0.0, 700.0, 1)
    return CornerDelta(
        c,
        CornerMetrics(**{**base, "time_s": 10.2, **lap}),  # type: ignore[arg-type]
        CornerMetrics(**{**base, **(ref or {})}),  # type: ignore[arg-type]
    )


def test_rules_on_constructed_metrics() -> None:
    def issues(**lap: object) -> list[str]:
        return [i[0] for i in find_issues(_delta(lap), CFG)]

    assert issues() == []
    assert issues(brake_rise_s=0.30) == ["brake_faster"]
    assert issues(trail_m=10.0) == ["trail_more"]
    assert issues(steering_corrections=4) == ["steering"]
    assert issues(exit_speed=38.0) == ["exit_speed"]
    # Freinage trop tard qui ruine la vitesse mini : « freine plus tôt »
    assert issues(brake_point_m=415.0, min_speed=28.5) == ["brake_earlier"]
    # Freiner plus tard que la référence sans rien perdre n'est pas un défaut
    assert issues(brake_point_m=415.0) == []
    # Pression faible + freinage tôt : la cause est la pression
    assert issues(brake_peak=0.6, brake_point_m=370.0) == ["brake_harder"]
    # Sous le seuil : rien
    assert issues(brake_point_m=396.0, min_speed=29.6, throttle_pickup_m=524.0) == []
    # Virage sans freinage (à fond) : pas de conseil de freinage
    flat = _delta({"brake_point_m": None, "brake_peak": 0.0}, {"brake_point_m": None,
                                                              "brake_peak": 0.0})  # fmt: skip
    assert find_issues(flat, CFG) == []


def test_small_loss_is_not_reported() -> None:
    d = _delta({"time_s": 10.02, "brake_point_m": 380.0})
    assert advise([d], CFG) == []


def test_process_lap_and_workers(tmp_path: Path) -> None:
    ref_lap, lap = laps_with(lap_style(c2=CornerStyle(brake_offset_m=-15.0)))
    first = process_lap(ref_lap, None, None, CFG, "fr", "", tmp_path / "a.npz", "a.npz")
    assert first.analysis is None and len(first.corners) == 8 and (tmp_path / "a.npz").is_file()
    inline = InlineWorker()
    inline.submit(process_lap, lap, first.grid, first.corners, CFG)
    res = inline.poll()[0]
    assert res.analysis.advice[0].issue == "brake_later" and inline.poll() == []
    invalid = process_lap(
        Lap(9, 80.0, 0.0, lap.track_length_m, lap.channels, offtrack=True),
        first.grid, first.corners, CFG,
    )  # fmt: skip
    assert invalid.analysis is None
    worker = ProcessWorker()  # vrai processus séparé à basse priorité
    try:
        worker.submit(process_lap, lap, first.grid, first.corners, CFG)
        out = worker.drain(60.0)
    finally:
        worker.close()
    assert len(out) == 1 and out[0].analysis.advice[0].corner == 3


def test_match_grid_resamples_reference() -> None:
    ref, grid, _ = grids(())
    shorter = GridLap(4190.0, ref.lap_time, {k: v[:4190] for k, v in ref.channels.items()})
    assert match_grid(shorter, grid).n == grid.n
    assert match_grid(ref, grid) is ref


def test_app_end_to_end_race(cfg: AppConfig) -> None:
    cfg.source.realtime = False
    speaker = NullSpeaker()
    app = SimEngineerApp(cfg, SyntheticSource(get_scenario("race")), speaker)
    sid = app.run()
    store = Store(cfg.data_dir)
    rows = store.laps(sid)
    assert len(rows) == 12 and not rows[-1].flags["complete"]
    assert not rows[6].valid and rows[6].flags["offtrack"]  # tour 7 : hors-piste
    said = [(int(u.priority), u.key) for u in speaker.said]
    assert (0, "flag_yellow") in said and (1, "pit_open") in said
    coaching = [k for p, k in said if p == 2]
    assert coaching and all(k.startswith("adv:T") for k in coaching)
    assert sum(k == "stopped:5:far" for _, k in said) == 9  # à chaque tour, tours 4 à 12
    assert len(store.kv_get("trackmap:Synthetic Ring")) == 8
    assert (cfg.data_dir / "alerts.jsonl").is_file()
    summary = build_session_summary(store, sid)
    assert summary["laps_valid"] == 10 and summary["best_lap_s"] < 93.0
    assert summary["theoretical_best_s"] <= summary["best_lap_s"] + 1e-6
    assert summary["top_corners"] and summary["theme"] in ("braking", "min_speed", "throttle")
    assert summary["reference"]["label"] == "meilleur tour de la séance"
    text = render_report(summary, "fr")
    assert "# Rapport de session — Synthetic Ring" in text and "## Virages où tu perds" in text
    assert "Session report" in render_report(summary, "en")
    label, analyses = reanalyze_session(store, sid, cfg.coach)
    assert label.startswith("tour ") and len(analyses) == 9
    assert app.stats()["mean_frame_ms"] < 2.0


def test_cli_run_analyze_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMENGINEER__LOGGING__LEVEL", "WARNING")
    runner = CliRunner()
    res = runner.invoke(
        cli_app, ["run", "--source", "synthetic", "--scenario", "clean", "--no-realtime",
                  "--null-voice"],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "[P1] Meilleur tour" in res.output and "Session 1 terminée" in res.output
    res = runner.invoke(cli_app, ["analyze"])
    assert res.exit_code == 0 and "Session 1" in res.output
    res = runner.invoke(cli_app, ["report"])
    assert res.exit_code == 0 and (tmp_path / "data/reports/session_00001.md").is_file()
