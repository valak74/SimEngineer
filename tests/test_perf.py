from __future__ import annotations

from simengineer.core.config import AppConfig
from simengineer.perf import PerfResult, measure, render
from simengineer.sources.synthetic import Scenario, SyntheticSource
from simengineer.telemetry.laps import LapSegmenter
from simengineer.telemetry.position import measure_position_precision
from simengineer.telemetry.resample import resample_lap


def test_perf_measure_runs_in_real_time(cfg: AppConfig) -> None:
    cfg.source.scenario = "perf"
    res = measure(cfg, seconds=2.0)
    assert isinstance(res, PerfResult) and res.frames == 120
    assert 1.8 < res.seconds < 3.5  # cadence temps réel respectée
    assert res.mean_frame_ms < 5.0 and res.rss_mb > 10.0
    text = render(res)
    assert "CPU collecteur" in text and "budget < 3.0 %" in text
    assert res.to_dict()["within_budget"] in (True, False)


def test_position_precision_measure() -> None:
    src = SyntheticSource(Scenario(n_laps=4))
    seg = LapSegmenter(src.session_info().track_length_m)
    grids = [resample_lap(lap) for f in src.frames() if (lap := seg.on_frame(f)) is not None]
    res = measure_position_precision(grids)
    assert res.available and res.laps == 3
    assert res.median_spread_m is not None and res.median_spread_m < 0.5
    assert res.quantization_m is not None and res.quantization_m > 0
    assert not measure_position_precision(grids[:1]).available


def test_perf_measure_with_ui_client(cfg: AppConfig) -> None:
    cfg.source.scenario = "perf"
    res = measure(cfg, seconds=1.5, with_ui=True)
    assert res.with_ui and res.frames == 90 and "interface" in render(res)
