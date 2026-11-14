from __future__ import annotations

from pathlib import Path

import numpy as np

from simengineer.core.vars import Flags
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    Scenario,
    SyntheticSource,
    build_lap_profile,
)
from simengineer.store.db import Store
from simengineer.telemetry.laps import Lap, LapSegmenter
from simengineer.telemetry.resample import load_grid_npz, resample_lap, save_lap_npz

LAP_TIME = build_lap_profile(DEFAULT_TRACK).lap_time


def run_laps(sc: Scenario) -> list[Lap]:
    src = SyntheticSource(sc)
    seg = LapSegmenter(src.session_info().track_length_m)
    return [lap for f in src.frames() if (lap := seg.on_frame(f)) is not None]


def test_segmentation_lap_times() -> None:
    laps = run_laps(Scenario(n_laps=4))
    assert [lap.number for lap in laps] == [1, 2, 3]  # le dernier tour n'est pas bouclé
    for lap in laps:
        assert lap.valid and lap.complete
        assert abs(lap.lap_time - LAP_TIME) < 0.01
        assert abs(lap.fuel_used - 2.5) < 0.01


def test_validity_flags() -> None:
    sc = Scenario(
        n_laps=6,
        offtracks=((1, 0.4, 0.42),),
        incidents=((2, 0.5, 2),),
        pit_stops=((3, 20.0),),
        flag_periods=((0.0, 10.0, int(Flags.YELLOW)),),
    )
    laps = run_laps(sc)
    by = {lap.number: lap for lap in laps}
    assert by[1].yellow and by[1].valid and not by[1].clean_for_fuel
    assert by[2].offtrack and not by[2].valid
    assert by[3].incident and not by[3].valid
    assert by[4].pit and not by[4].valid
    assert by[5].pit and not by[5].valid  # tour de sortie des stands


def test_mid_lap_start_is_incomplete() -> None:
    src = SyntheticSource(Scenario(n_laps=3))
    seg = LapSegmenter(DEFAULT_TRACK.length_m)
    laps = [lap for f in list(src.frames())[2000:] if (lap := seg.on_frame(f)) is not None]
    assert not laps[0].complete and laps[1].complete
    last = seg.flush()
    assert last is not None and not last.complete


def test_resample_grid_1m() -> None:
    lap = run_laps(Scenario(n_laps=2))[0]
    grid = resample_lap(lap)
    assert grid.n == int(DEFAULT_TRACK.length_m) and grid.step == 1.0
    assert grid.ch("time")[0] == 0.0
    assert np.all(np.diff(grid.ch("time")) > 0)
    prof = build_lap_profile(DEFAULT_TRACK)
    assert np.max(np.abs(grid.ch("speed") - prof.v[::2])) < 1.5
    assert abs(grid.ch("time")[-1] + 1.0 / grid.ch("speed")[-1] - lap.lap_time) < 0.01


def test_npz_and_store_roundtrip(tmp_path: Path) -> None:
    src = SyntheticSource(Scenario(n_laps=3))
    store = Store(tmp_path)
    sid = store.create_session(src.session_info(), src.name)
    seg = LapSegmenter(src.session_info().track_length_m)
    for f in src.frames():
        lap = seg.on_frame(f)
        if lap is not None:
            store.add_lap(sid, lap, resample_lap(lap))
    rows = store.laps(sid)
    assert len(rows) == 2 and all(r.valid for r in rows)
    assert rows[0].npz_path is not None
    grid = store.load_grid(rows[0].npz_path)
    assert abs(grid.lap_time - rows[0].lap_time) < 1e-9 and grid.meta["valid"] is True
    assert store.session(sid).track == "Synthetic Ring"
    assert store.last_session_id() == sid
    store.kv_set("k", {"a": 1})
    assert store.kv_get("k") == {"a": 1} and store.kv_get("x", 3) == 3
    p = tmp_path / "x.npz"
    save_lap_npz(p, grid)
    assert load_grid_npz(p).n == grid.n
