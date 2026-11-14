"""Assemblage de l'application à partir de la configuration."""

from __future__ import annotations

from collections.abc import Callable

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.core.log import get_logger
from simengineer.sources.factory import make_source
from simengineer.voice.arbiter import Utterance
from simengineer.voice.factory import make_speaker

log = get_logger("runtime")


def _log_latency(utt: Utterance, ms: float) -> None:
    if utt.priority == 0:
        level = log.warning if ms > 200.0 else log.debug
        level("Alerte %r : donnée -> son en %.0f ms", utt.key, ms)


def build_app(cfg: AppConfig, echo: Callable[[str], None] | None = None) -> SimEngineerApp:
    speaker = make_speaker(cfg, echo=echo, on_started=_log_latency)
    return SimEngineerApp(cfg, make_source(cfg.source), speaker)
