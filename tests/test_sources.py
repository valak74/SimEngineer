from __future__ import annotations

from pathlib import Path

import numpy as np

from simengineer.core.vars import VARS, CarLeftRight
from simengineer.sources.ibt_format import read_ibt, write_ibt
from simengineer.sources.ibt_replay import IbtReplaySource
from simengineer.sources.scenarios import SCENARIOS, get_scenario
from simengineer.sources.session_yaml import parse_session_yaml, session_info_to_yaml
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    Scenario,
    SyntheticSource,
    build_lap_profile,
    generate,
    lap_style,
)


def test_profile_is_plausible_and_deterministic() -> None:
    p = build_lap_profile(DEFAULT_TRACK)
    assert 80 < p.lap_time < 110
    assert p.v.min() == min(c.min_speed for c in DEFAULT_TRACK.corners)
    assert p.v.max() <= DEFAULT_TRACK.top_speed
    assert np.array_equal(
        generate(Scenario(n_laps=1)).columns[VARS["speed"].name],
        generate(Scenario(n_laps=1)).columns[VARS["speed"].name],
    )


def test_injected_faults_cost_time() -> None:
    base = build_lap_profile(DEFAULT_TRACK).lap_time
    for style in (
        CornerStyle(brake_offset_m=-15),
        CornerStyle(min_speed_delta=-2),
        CornerStyle(throttle_offset_m=15),
    ):
        assert build_lap_profile(DEFAULT_TRACK, lap_style(c2=style)).lap_time > base + 0.1


def test_frames_stream() -> None:
    frames = list(SyntheticSource(Scenario(n_laps=2)).frames())
    assert frames[0].lap == 1 and frames[-1].lap == 2
    assert frames[0].slow is not None and frames[1].slow is None
    assert abs(frames[1].session_time - frames[0].session_time - 1 / 60) < 1e-9
    assert frames[0].car_idx_lap_dist_pct is not None
    assert frames[0].session_laps_remain == -1


def test_all_scenarios_generate() -> None:
    for name in SCENARIOS:
        raw = generate(get_scenario(name))
        assert raw.n_frames > 1000


def test_overtake_scenario_has_side_calls() -> None:
    raw = generate(get_scenario("overtake"))
    clr = raw.columns[VARS["car_left_right"].name]
    assert int(CarLeftRight.CAR_RIGHT) in clr


def test_session_yaml_roundtrip() -> None:
    info = generate(Scenario(n_laps=1, race_laps=20, session_type="Race")).info
    back = parse_session_yaml(session_info_to_yaml(info))
    assert back.track_name == info.track_name
    assert back.track_length_m == info.track_length_m
    assert back.car_name == info.car_name
    assert back.session_laps == 20 and back.session_type == "Race"
    assert back.fuel_capacity_l == info.fuel_capacity_l


def test_ibt_roundtrip(tmp_path: Path) -> None:
    raw = generate(get_scenario("overtake"))
    path = tmp_path / "s.ibt"
    write_ibt(raw, path)
    back = read_ibt(path)
    assert back.n_frames == raw.n_frames and back.tick_rate == 60
    assert set(back.columns) == set(raw.columns)
    for key in ("session_time", "lap", "car_left_right", "car_idx_lap_dist_pct", "on_pit_road"):
        name = VARS[key].name
        assert np.allclose(back.columns[name], raw.columns[name], atol=1e-5), key
    frames = list(IbtReplaySource(path).frames())
    assert len(frames) == raw.n_frames
    assert frames[100].speed > 10
