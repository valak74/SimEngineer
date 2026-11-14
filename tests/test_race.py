from __future__ import annotations

import dataclasses
import shutil

import numpy as np

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import Flags
from simengineer.engineer.race import History, RaceMonitor
from simengineer.sources.scenarios import LAP_TIME, get_scenario
from simengineer.sources.synthetic import CornerStyle, Scenario, SyntheticSource, lap_style
from simengineer.telemetry.laps import Lap
from simengineer.voice.arbiter import Priority
from simengineer.voice.speaker import NullSpeaker
from simengineer.voice.templates import MAX_WORDS_SHORT, render, word_count

RACE = SessionInfo(
    session_type="Race", session_laps=12, incident_limit=17, pit_speed_limit_kmh=60.0,
    track_length_m=4200.0,
)  # fmt: skip


def frame(t: float = 10.0, **kw: object) -> Frame:
    base: dict[str, object] = dict(
        tick=0, session_time=t, lap=1, lap_dist_pct=0.1, speed=50.0, throttle=1.0, brake=0.0,
        steering=0.0, fuel_level=26.0,
    )  # fmt: skip
    base.update(kw)
    return Frame(**base)  # type: ignore[arg-type]


def lap(n: int = 1) -> Lap:
    return Lap(n, 92.0, 0.0, 4200.0, {})


def keys(utts: list) -> list[str]:  # type: ignore[type-arg]
    return [u.key for u in utts]


def test_briefing_uses_history() -> None:
    m = RaceMonitor(RACE, history=History(2.5, 92.0, 20))
    (b,) = m.on_frame(frame(fuel_level=26.0))
    assert b.text == (
        "Course de 12 tours. Il faut environ 31 litres, tu en as 26 : un arrêt sera nécessaire."
        " Limite d'incidents 17."
    )
    assert m.on_frame(frame(11.0)) == []  # une seule fois
    rich = RaceMonitor(RACE, history=History(2.5, 92.0, 20)).briefing(frame(fuel_level=40.0))
    assert "assez pour finir" in rich.text
    save = RaceMonitor(RACE, history=History(2.5, 92.0, 20)).briefing(frame(fuel_level=29.5))
    assert "économiser" in save.text
    timed = dataclasses.replace(RACE, session_laps=None, session_time_s=1800.0)
    t = RaceMonitor(timed, history=History(2.5, 92.0, 20)).briefing(frame(fuel_level=60.0))
    assert t.text.startswith("Course de 30 minutes, environ 21 tours.")
    unknown = RaceMonitor(RACE).briefing(frame())
    assert "Pas encore de conso connue" in unknown.text


def test_incidents_and_limit_warning() -> None:
    m = RaceMonitor(RACE)
    m._briefed = True
    assert m.on_frame(frame(incident_count=0)) == []
    assert [u.text for u in m.on_frame(frame(incident_count=4))] == ["Incident : 4 sur 17"]
    out = m.on_frame(frame(incident_count=14))
    assert keys(out) == ["incident", "incident_warning"]
    assert out[1].text == "Attention, plus que 3 points avant la disqualification"
    nolimit = RaceMonitor(dataclasses.replace(RACE, incident_limit=None))
    nolimit._briefed = True
    nolimit.on_frame(frame(incident_count=0))
    assert nolimit.on_frame(frame(incident_count=2))[0].text == "Incident : 2 au total"


def test_pit_lane_limit_and_speeding() -> None:
    m = RaceMonitor(RACE)
    m._briefed = True
    m.on_frame(frame(on_pit_road=False))
    (u,) = m.on_frame(frame(t=11.0, on_pit_road=True, speed=59 / 3.6))
    assert u.text == "Voie des stands, limite 60"
    assert m.on_frame(frame(t=11.1, on_pit_road=True, speed=64 / 3.6)) == []  # < 0,3 s
    (v,) = m.on_frame(frame(t=11.5, on_pit_road=True, speed=64 / 3.6))
    assert v.priority is Priority.P0 and word_count(v.text) <= MAX_WORDS_SHORT
    assert m.on_frame(frame(t=12.0, on_pit_road=True, speed=64 / 3.6)) == []  # délai 5 s


def standings(pos: list[int], f2: list[float]) -> dict[str, object]:
    return {"car_idx_position": np.array(pos), "car_idx_f2_time": np.array(f2, dtype=float)}


def test_position_gaps_and_trend() -> None:
    m = RaceMonitor(RACE)
    m._briefed = True
    m.on_frame(frame(t=1.0, **standings([3, 1, 2, 4], [10.0, 0.0, 8.0, 11.0])))
    assert m.status.start_position == 3
    assert m.status.gap_ahead_s == 2.0 and m.status.gap_behind_s == 1.0
    m.on_lap(lap(1), None)
    # Derrière (n°3) reprend 0,3 s par tour pendant 3 tours
    for i, behind in enumerate([0.7, 0.4, 0.1]):
        m.on_frame(frame(t=2.0 + i, **standings([3, 1, 2, 4], [10.0, 0.0, 8.0, 10.0 + behind])))
        out = m.on_lap(lap(i + 2), None)
    assert [u.text for u in out] == ["Derrière à 0,1, il reprend 0,3 par tour"]
    m.on_frame(frame(t=9.0, **standings([2, 1, 3, 4], [8.0, 0.0, 8.5, 11.0])))
    assert [u.text for u in m.on_lap(lap(6), None)] == ["P2, tu gagnes 1 place"]
    assert m.status.gained == 1


def test_laps_to_go_debrief_and_minutes() -> None:
    m = RaceMonitor(RACE, best_lap=lambda: 92.1)
    m._briefed = True
    m.on_frame(frame(t=1.0, **standings([3, 1, 2], [9.0, 0.0, 5.0])))
    assert keys(m.on_lap(lap(7), frame(session_laps_remain=5))) == ["togo5"]
    m.on_frame(frame(t=2.0, incident_count=0, **standings([1, 2, 3], [0.0, 3.0, 5.0])))
    m.on_frame(frame(t=3.0, session_flags=int(Flags.CHECKERED)))
    (d,) = m.on_lap(lap(12), None)
    assert (
        d.text
        == "Course terminée, P1, 2 places gagnées. Meilleur tour 1:32,1. 0 points d'incident."
    )
    timed = RaceMonitor(dataclasses.replace(RACE, session_laps=None, session_time_s=1800.0))
    timed._briefed = True
    out = timed.on_frame(frame(t=1500.0, session_laps_remain=-1, session_time_remain=299.0))
    assert [u.text for u in out] == ["Plus que 5 minutes"]


def test_qualifying_flow_and_sector_deltas(cfg: AppConfig) -> None:
    cfg.source.realtime = False
    slow = lap_style(c2=CornerStyle(brake_offset_m=-14))
    sc = Scenario(
        n_laps=4, session_type="Lone Qualify", lap_styles=((0, slow), (1, slow), (3, slow))
    )
    speaker = NullSpeaker()
    SimEngineerApp(cfg, SyntheticSource(sc), speaker).run()
    texts = [u.text for u in speaker.said]
    assert "Tour lancé" in texts
    assert "Secteur 1, dans le temps" in texts  # 2e tour identique au 1er
    assert "Secteur 2, moins 0,47" in texts  # 3e tour : défaut corrigé, gain au T03
    assert "Secteur 2, plus 0,47" in texts  # 4e tour : défaut revenu
    for t in texts:
        if t.startswith("Secteur"):
            assert word_count(t) <= MAX_WORDS_SHORT
    assert render("quali_outlap", "fr").startswith("Tour de lancement")


def test_race_end_to_end_with_history(cfg: AppConfig) -> None:
    cfg.source.realtime = False
    SimEngineerApp(cfg, SyntheticSource(Scenario(n_laps=4)), NullSpeaker()).run()  # historique
    base = get_scenario("race")
    sc = dataclasses.replace(
        base, incident_limit=17, pit_stops=((5, 10.0),), pit_drive_kmh=72.0,
        incidents=((6, 0.36, 1), (8, 0.5, 4), (9, 0.2, 8)),
        flag_periods=(*base.flag_periods, (11 * LAP_TIME, 12 * LAP_TIME + 5, int(Flags.CHECKERED))),
    )  # fmt: skip
    speaker = NullSpeaker()
    app = SimEngineerApp(cfg, SyntheticSource(sc), speaker)
    app.run()
    said = {u.key: u.text for u in speaker.said}
    assert said["briefing"].startswith("Course de 12 tours. Il faut environ 31 litres")
    assert said["pit_lane_limit"] == "Voie des stands, limite 60" and "pit_speeding" in said
    assert said["incident_warning"] == "Attention, plus que 4 points avant la disqualification"
    assert said["debrief"].startswith("Course terminée, P")
    assert app.engineer is not None and app.engineer.state.race.finished
    shutil.rmtree(cfg.data_dir, ignore_errors=True)
