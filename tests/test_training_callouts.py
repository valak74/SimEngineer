from __future__ import annotations

from pathlib import Path

import pytest

from simengineer.app import SimEngineerApp
from simengineer.coach.callouts import (
    CalloutPoint,
    CornerCallouts,
    callout_points,
    training_focus,
)
from simengineer.coach.corners import detect_corners
from simengineer.coach.pipeline import analyze_lap
from simengineer.core.config import AppConfig, CoachConfig
from simengineer.core.types import Frame
from simengineer.reference.importer import import_ibt
from simengineer.sources.ibt_format import write_ibt
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    Scenario,
    SyntheticSource,
    generate,
    lap_style,
)
from simengineer.store.db import Store
from simengineer.telemetry.laps import LapSegmenter
from simengineer.telemetry.resample import GridLap, resample_lap
from simengineer.voice.arbiter import Priority
from simengineer.voice.speaker import NullSpeaker
from simengineer.voice.templates import TEMPLATES, render, word_count

L = DEFAULT_TRACK.length_m
CFG = CoachConfig()
SLOW_T3 = lap_style(c2=CornerStyle(brake_offset_m=-14.0), c6=CornerStyle(throttle_offset_m=12.0))


def grids(style: tuple = ()) -> list[GridLap]:  # type: ignore[type-arg]
    src = SyntheticSource(Scenario(n_laps=4, lap_styles=((1, style),)))
    seg = LapSegmenter(L)
    return [resample_lap(lap) for f in src.frames() if (lap := seg.on_frame(f)) is not None]


def frame(pos_m: float, speed: float = 50.0, pit: bool = False) -> Frame:
    return Frame(0, 0.0, 1, pos_m / L, speed, 1.0, 0.0, 0.0, on_pit_road=pit)


def test_callout_points_before_braking_on_the_straight() -> None:
    ref = grids()[0]
    corners = detect_corners(ref, CFG)
    points = {p.corner: p for p in callout_points(ref, corners, CFG)}
    assert set(points) == set(range(1, 9))  # circuit de démo : toutes les lignes droites suffisent
    for c in corners:
        p = points[c.number]
        prev_end = corners[c.number - 2].end_m if c.number > 1 else corners[-1].end_m - L
        lead = (p.brake_m - p.trigger_m) % L
        assert p.brake_m < c.apex_m
        assert 100.0 < lead < 3.6 * 75.0  # ~3,5 s à vitesse de ligne droite
        assert (p.trigger_m - prev_end) % L > 0  # jamais dans le virage précédent
    short = CoachConfig(callout_min_straight_s=20.0)
    assert callout_points(ref, corners, short) == []  # enchaînements : pas de rappel


def test_callout_fires_once_per_lap_and_handles_line_crossing() -> None:
    pts = [CalloutPoint(1, 4100.0, 4190.0), CalloutPoint(3, 1200.0, 1350.0)]
    co = CornerCallouts(pts, L, [1, 3], "braking")
    said = []
    for pos in [*range(1000, 4200, 20), *range(0, 1500, 20), *range(1500, 4200, 20), 30]:
        said += [u.key for u in co.on_frame(frame(float(pos)))]
    assert said == ["callout:T03", "callout:T01", "callout:T03", "callout:T01"]
    co2 = CornerCallouts(pts, L, [3], "braking")
    assert co2.on_frame(frame(1100)) == [] and len(co2.on_frame(frame(1250))) == 1
    assert co2.on_frame(frame(1100)) == []  # retour en arrière (reset) : rien
    assert co2.on_frame(frame(1250)) != []
    co3 = CornerCallouts(pts, L, [3], "braking")
    co3.on_frame(frame(1100, pit=True))
    assert co3.on_frame(frame(1250, pit=True)) == []  # stand : silence
    co3.on_frame(frame(1100, speed=5.0))
    assert co3.on_frame(frame(1250, speed=5.0)) == []  # trop lent (sortie, neutralisation)


def test_callout_utterance_properties() -> None:
    co = CornerCallouts([CalloutPoint(3, 1200.0, 1350.0)], L, [3], "trail")
    co.on_frame(frame(1100))
    (u,) = co.on_frame(frame(1250))
    assert u.priority is Priority.P2 and u.ttl_s is not None and u.ttl_s <= 3.0
    assert u.text == "Virage 3, relâche le frein progressivement"
    for key in [k for k in TEMPLATES if k.startswith("call_")]:
        for lang in ("fr", "en"):
            assert word_count(render(key, lang, n=12)) <= 8, key


def test_messages_follow_last_analysis() -> None:
    g = grids(SLOW_T3)
    ref, lap = g[0], g[1]
    corners = detect_corners(ref, CFG)
    co = CornerCallouts(callout_points(ref, corners, CFG), L, [3, 7], "braking")
    assert co.message(3) == "Virage 3, attaque franche au freinage"
    co.update_from_analysis(analyze_lap(lap, ref, corners, CFG))
    assert co.message(3) == "Virage 3, freine 12 mètres plus tard"
    assert co.message(7) == "Virage 7, gaz 12 mètres plus tôt"
    co.update_from_analysis(analyze_lap(g[2], ref, corners, CFG))  # défauts corrigés
    assert co.message(3) == "Virage 3, c'était bien, pareil"


def test_training_focus_priority() -> None:
    assert training_focus(["T03"], ["T05"], None, 3) == [3]
    assert training_focus([], ["T05", "T07"], None, 1) == [5]
    assert training_focus([], [], None, 3) == []


@pytest.fixture
def with_ref(cfg: AppConfig, tmp_path: Path) -> AppConfig:
    cfg.source.realtime = False
    ibt = tmp_path / "ref.ibt"
    write_ibt(generate(Scenario(n_laps=3)), ibt)
    import_ibt(Store(cfg.data_dir), ibt, "mon tour", "personnel")
    return cfg


def run(cfg: AppConfig, sc: Scenario) -> tuple[SimEngineerApp, list[tuple[float, str, str]]]:
    speaker = NullSpeaker()
    app = SimEngineerApp(cfg, SyntheticSource(sc), speaker)
    said: list[tuple[float, str, str]] = []
    pos = [0.0]

    def track(f: Frame) -> None:
        pos[0] = f.lap_dist_pct * L
        while len(said) < len(speaker.said):
            u = speaker.said[len(said)]
            said.append((pos[0], u.key, u.text))

    app.add_poll_hook(lambda: None)
    app.add_frame_hook(track)
    app.run()
    return app, said


def test_training_mode_end_to_end(with_ref: AppConfig) -> None:
    cfg = with_ref
    run(cfg, Scenario(n_laps=4, base_style=SLOW_T3))  # séance 1 : construit le profil
    cfg.coach.training_mode = True
    app, said = run(cfg, Scenario(n_laps=4, base_style=SLOW_T3))
    training = app.coach.training
    assert training is not None and training.theme == "braking"
    assert training.active_corners == [3]  # programme : freinage, virage 3
    keys = [k for _, k, _ in said]
    assert keys[0] == "training_on"
    assert said[0][2] == "Mode entraînement : freinage, virages 3"
    calls = [(p, t) for p, k, t in said if k == "callout:T03"]
    assert len(calls) >= 3  # à chaque tour (le 1er est générique, puis chiffré)
    assert calls[0][1] == "Virage 3, attaque franche au freinage"
    assert calls[-1][1] == "Virage 3, freine 12 mètres plus tard"
    point = training._points[0]
    for p, _ in calls:
        assert point.trigger_m <= p < point.brake_m  # dit dans la ligne droite, avant le frein
    assert not any(k.startswith("adv:T03") for k in keys)  # pas répété au débrief
    assert any(k.startswith("adv:T07") for k in keys)  # les autres virages restent débriefés


def test_training_mode_without_history_starts_after_first_analysis(with_ref: AppConfig) -> None:
    cfg = with_ref
    cfg.coach.training_mode = True
    app, said = run(cfg, Scenario(n_laps=4, base_style=SLOW_T3))
    assert app.coach.training is not None and app.coach.training.active_corners == [3, 7]
    keys = [k for _, k, _ in said]
    assert "training_on" in keys and "callout:T03" in keys and "callout:T07" in keys


def test_training_off_by_default(with_ref: AppConfig) -> None:
    app, said = run(with_ref, Scenario(n_laps=3, base_style=SLOW_T3))
    assert app.coach.training is None and not any(k.startswith("callout") for _, k, _ in said)
