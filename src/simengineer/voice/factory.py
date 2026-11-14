"""Assemblage de la chaîne vocale selon la configuration."""

from __future__ import annotations

from collections.abc import Callable

from simengineer.core.config import AppConfig
from simengineer.core.log import get_logger
from simengineer.voice.arbiter import Speaker
from simengineer.voice.speaker import NullSpeaker, OnStarted, TTSSpeaker
from simengineer.voice.templates import danger_phrases, static_phrases
from simengineer.voice.tts import CachedTTS, PiperTTS

log = get_logger("voice")


def make_speaker(
    cfg: AppConfig,
    echo: Callable[[str], None] | None = None,
    on_started: OnStarted | None = None,
    prewarm: bool = True,
) -> Speaker:
    """Speaker Piper + cache + périphérique choisi ; `NullSpeaker` si tts_backend = "null"."""
    if cfg.voice.tts_backend == "null":
        return NullSpeaker(echo)
    from simengineer.voice.audio import SoundDeviceOutput

    piper = PiperTTS({"fr": cfg.voice.piper_voice_fr, "en": cfg.voice.piper_voice_en})
    tts = CachedTTS(piper, cfg.data_dir / "tts_cache")
    if prewarm:
        phrases = static_phrases(cfg.language) + danger_phrases(cfg.language)
        ready = tts.prewarm(phrases, cfg.language)
        log.info("Cache vocal : %d/%d phrases prêtes", ready, len(phrases))
    return TTSSpeaker(tts, SoundDeviceOutput(cfg.voice.output_device), cfg.language, on_started)
