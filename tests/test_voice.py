from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from simengineer.core.events import EventBus, SessionEnded
from simengineer.voice.arbiter import Priority, SpeechArbiter, SpeechContext, Utterance
from simengineer.voice.audio import AudioUnavailableError, NullAudioOutput, resolve_device
from simengineer.voice.speaker import NullSpeaker, TTSSpeaker
from simengineer.voice.templates import (
    MAX_WORDS_SHORT,
    TEMPLATES,
    danger_phrases,
    fmt_delta,
    fmt_lap_time,
    render,
    static_phrases,
    word_count,
)
from simengineer.voice.tts import Audio, CachedTTS, NullTTS, PiperTTS, TTSUnavailableError


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make() -> tuple[SpeechArbiter, NullSpeaker, Clock]:
    clock, speaker = Clock(), NullSpeaker()
    return SpeechArbiter(speaker, clock), speaker, clock


def u(p: Priority, key: str, text: str = "un deux", **kw: float) -> Utterance:
    return Utterance(p, key, text, **kw)  # type: ignore[arg-type]


def test_event_bus() -> None:
    bus, seen = EventBus(), []
    bus.subscribe(SessionEnded, seen.append)
    bus.publish(SessionEnded(3))
    bus.publish("ignoré")
    assert seen == [SessionEnded(3)]


def test_short_templates_are_short_in_both_languages() -> None:
    p0 = [k for k in TEMPLATES if k.startswith(("car_", "cars_", "two_", "still_", "clear"))]
    p0 += ["stopped_ahead", "offtrack_ahead", "accident_ahead", "offtrack_self"]
    p0 += ["flag_yellow", "flag_fcy", "flag_red", "flag_debris", "flag_black"]
    p2 = [k for k in TEMPLATES if k.startswith("adv_")]
    for key in p0 + p2:
        for lang in ("fr", "en"):
            text = render(key, lang, dist=300, n=12, d=15, kmh=5)
            assert word_count(text) <= MAX_WORDS_SHORT, (key, lang, text)


def test_formatting() -> None:
    assert fmt_lap_time(92.34) == "1:32,3" and fmt_lap_time(92.34, "en") == "1:32.3"
    assert fmt_delta(-0.234) == "moins 0,23" and fmt_delta(0.5, "en") == "plus 0.50"
    assert render("stopped_ahead", "fr", dist=300) == "Voiture arrêtée devant, 300 mètres"
    assert "Libre" in static_phrases("fr") and len(danger_phrases("en")) == 39


def test_priority_order_and_single_voice() -> None:
    arb, sp, _ = make()
    arb.submit(u(Priority.P3, "chat"))
    arb.submit(u(Priority.P2, "coach"))
    arb.submit(u(Priority.P1, "fuel"))
    sp.busy_flag = True
    assert arb.tick() is None  # une seule voix à la fois
    sp.busy_flag = False
    assert [arb.tick().key for _ in range(3)] == ["fuel", "coach", "chat"]  # type: ignore[union-attr]
    assert arb.tick() is None


def test_p0_cuts_everything_and_requeues_p1() -> None:
    arb, sp, _ = make()
    arb.submit(u(Priority.P1, "fuel"))
    arb.tick()
    sp.busy_flag = True  # l'info carburant est en cours de lecture
    assert arb.submit(u(Priority.P0, "left", "Voiture à gauche"))
    assert sp.stops == 1 and sp.said[-1].key == "left"
    assert arb.tick().key == "fuel"  # type: ignore[union-attr]  # redite après le danger


def test_two_dangers_do_not_cut_each_other() -> None:
    arb, sp, _ = make()
    arb.submit(u(Priority.P0, "left"))
    sp.busy_flag = True
    arb.submit(u(Priority.P0, "stopped"))
    assert sp.stops == 0 and len(sp.said) == 1
    sp.busy_flag = False
    assert arb.tick().key == "stopped"  # type: ignore[union-attr]


def test_anti_repeat_by_key() -> None:
    arb, sp, clock = make()
    assert arb.submit(u(Priority.P0, "yellow", cooldown_s=10))
    clock.t += 5
    assert not arb.submit(u(Priority.P0, "yellow", cooldown_s=10))
    clock.t += 6
    assert arb.submit(u(Priority.P0, "yellow", cooldown_s=10))
    assert len(sp.said) == 2


def test_queue_dedup_keeps_latest() -> None:
    arb, sp, _ = make()
    arb.submit(u(Priority.P1, "fuel", "ancien"))
    arb.submit(u(Priority.P1, "fuel", "nouveau"))
    assert arb.pending() == 1
    arb.tick()
    assert sp.said[0].text == "nouveau"


def test_expiry() -> None:
    arb, sp, clock = make()
    arb.submit(u(Priority.P1, "gap", ttl_s=5))
    clock.t += 6
    assert arb.tick() is None and arb.pending() == 0 and not sp.said


def test_coaching_only_on_straight() -> None:
    arb, _sp, _ = make()
    arb.submit(u(Priority.P2, "t4"))
    assert arb.tick(SpeechContext(on_straight=False)) is None
    assert arb.tick(SpeechContext(braking=True)) is None
    assert arb.tick(SpeechContext(in_battle=True)) is None
    arb.submit(u(Priority.P3, "chat"))
    assert arb.tick(SpeechContext(braking=True)).key == "chat"  # type: ignore[union-attr]
    assert arb.tick(SpeechContext()).key == "t4"  # type: ignore[union-attr]


def test_long_coaching_rejected() -> None:
    arb, _, _ = make()
    assert not arb.submit(u(Priority.P2, "long", "un deux trois quatre cinq six sept huit neuf"))
    assert arb.submit(u(Priority.P1, "long", "un deux trois quatre cinq six sept huit neuf"))


class CountingTTS:
    voice_id = "count"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, lang: str) -> Audio:
        self.calls += 1
        return Audio(np.ones(160, dtype=np.int16) * len(text), 16000)


def test_tts_cache_memory_and_disk(tmp_path: Path) -> None:
    backend = CountingTTS()
    tts = CachedTTS(backend, tmp_path)
    a = tts.synthesize("Libre", "fr")
    assert tts.synthesize("Libre", "fr") is a and backend.calls == 1
    tts.synthesize("Libre", "en")
    assert backend.calls == 2
    fresh = CachedTTS(CountingTTS(), tmp_path)  # nouveau processus : cache disque
    assert fresh.is_cached("Libre", "fr")
    b = fresh.synthesize("Libre", "fr")
    assert np.array_equal(a.pcm, b.pcm) and fresh.hits == 1 and fresh.misses == 0
    assert fresh.prewarm(static_phrases("fr"), "fr") == len(static_phrases("fr"))


def test_piper_missing_voice_is_clear_error() -> None:
    with pytest.raises(TTSUnavailableError):
        PiperTTS({"fr": "absent.onnx"}).synthesize("Libre", "fr")
    assert CachedTTS(PiperTTS({"fr": "absent.onnx"}), None).prewarm(["Libre"], "fr") == 0


def test_tts_speaker_plays_and_measures_latency() -> None:
    out, lat = NullAudioOutput(), []
    sp = TTSSpeaker(NullTTS(0.01), out, on_started=lambda utt, ms: lat.append(ms))
    sp.say(Utterance(Priority.P0, "left", "Voiture à gauche", data_time=time.perf_counter()))
    assert sp.busy()
    assert sp.wait_idle()
    assert len(out.played) == 1 and len(lat) == 1 and lat[0] < 200.0
    sp.close()


def test_resolve_device() -> None:
    devs = [
        {"index": 0, "name": "Micro USB", "inputs": 1, "outputs": 0},
        {"index": 1, "name": "Casque (Realtek)", "inputs": 0, "outputs": 2},
    ]
    assert resolve_device("", devs) is None
    assert resolve_device("1", devs) == 1
    assert resolve_device("casque", devs) == 1
    assert resolve_device("micro", devs, "inputs") == 0
    with pytest.raises(AudioUnavailableError):
        resolve_device("inconnu", devs)
