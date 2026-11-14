from __future__ import annotations

from pathlib import Path

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.reference.importer import import_ibt
from simengineer.sources.ibt_format import write_ibt
from simengineer.sources.synthetic import (
    CornerStyle,
    Scenario,
    SyntheticSource,
    generate,
    lap_style,
)
from simengineer.store.db import Store
from simengineer.voice.arbiter import Priority, SpeechArbiter, Utterance
from simengineer.voice.speaker import NullSpeaker
from simengineer.voice.templates import MAX_WORDS_SHORT, word_count


def test_trend_detects_a_corner_getting_worse(cfg: AppConfig, tmp_path: Path) -> None:
    cfg.source.realtime = False
    ibt = tmp_path / "ref.ibt"
    write_ibt(generate(Scenario(n_laps=3)), ibt)
    import_ibt(Store(cfg.data_dir), ibt, "mon tour", "personnel")
    worse = lap_style(c2=CornerStyle(brake_offset_m=-14.0))
    sc = Scenario(n_laps=10, lap_styles=tuple((i, worse) for i in range(5, 10)))
    speaker = NullSpeaker()
    SimEngineerApp(cfg, SyntheticSource(sc), speaker).run()
    trends = [u for u in speaker.said if u.key.startswith("trend:")]
    assert [u.text for u in trends] == ["Virage 3, 0,5 seconde perdue depuis 3 tours"]
    assert trends[0].priority is Priority.P2 and word_count(trends[0].text) <= MAX_WORDS_SHORT


def test_no_trend_when_driving_is_stable(cfg: AppConfig) -> None:
    cfg.source.realtime = False
    speaker = NullSpeaker()
    SimEngineerApp(cfg, SyntheticSource(Scenario(n_laps=9)), speaker).run()
    assert not any(u.key.startswith("trend:") for u in speaker.said)


def test_push_to_talk_silences_the_engineer_but_not_dangers() -> None:
    now = [0.0]
    speaker = NullSpeaker()
    arb = SpeechArbiter(speaker, lambda: now[0])
    arb.submit(Utterance(Priority.P1, "fuel", "Carburant pour 9 tours"))
    arb.tick()
    speaker.busy_flag = True  # l'ingénieur parle…
    arb.set_driver_talking(True)  # …le pilote appuie sur le bouton
    arb.submit(Utterance(Priority.P2, "adv", "Virage 3, freine plus tard"))
    assert arb.tick() is None and speaker.stops == 1  # coupé, rien de nouveau
    speaker.busy_flag = False
    assert arb.tick() is None  # toujours silencieux tant que le bouton est enfoncé
    arb.submit(Utterance(Priority.P0, "left", "Voiture à gauche"))
    assert speaker.said[-1].key == "left"  # le danger passe quand même
    arb.set_driver_talking(False)
    assert arb.tick() is not None and speaker.said[-1].key == "adv"  # reprise au relâché


def test_p0_is_not_cut_by_the_button() -> None:
    speaker = NullSpeaker()
    arb = SpeechArbiter(speaker)
    arb.submit(Utterance(Priority.P0, "left", "Voiture à gauche"))
    speaker.busy_flag = True
    arb.set_driver_talking(True)
    arb.tick()
    assert speaker.stops == 0
