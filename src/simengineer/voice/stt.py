"""Reconnaissance vocale : interface `STTBackend`, faster-whisper local sur CPU.

Le modèle ne travaille que sur l'audio capté pendant l'appui du bouton push-to-talk.
L'interface permet de passer à une API de transcription si le CPU est trop sollicité.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from simengineer.core.log import get_logger

log = get_logger("stt")

STT_SAMPLE_RATE = 16000


class STTBackend(Protocol):
    def transcribe(self, pcm: NDArray[np.int16], sample_rate: int, lang: str) -> str: ...


class STTUnavailableError(RuntimeError):
    pass


class NullSTT:
    """Aucune transcription (tests, mode sans micro) : renvoie un texte fixe."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def transcribe(self, pcm: NDArray[np.int16], sample_rate: int, lang: str) -> str:
        return self.text


class FasterWhisperSTT:
    """faster-whisper `small` en int8 sur CPU (import et chargement paresseux)."""

    def __init__(self, model: str = "small", compute_type: str = "int8", threads: int = 2) -> None:
        self._name = model
        self._compute = compute_type
        self._threads = threads  # peu de threads : on ne vole pas le CPU du simulateur
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTUnavailableError(
                "faster-whisper absent : pip install simengineer[voice]"
            ) from exc
        self._model = WhisperModel(
            self._name, device="cpu", compute_type=self._compute, cpu_threads=self._threads
        )

    def transcribe(self, pcm: NDArray[np.int16], sample_rate: int, lang: str) -> str:
        self.load()
        audio = pcm.astype(np.float32) / 32768.0
        if sample_rate != STT_SAMPLE_RATE:
            n = int(len(audio) * STT_SAMPLE_RATE / sample_rate)
            audio = np.interp(
                np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio
            ).astype(np.float32)
        segments, _info = self._model.transcribe(
            audio, language=lang, beam_size=1, condition_on_previous_text=False
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
