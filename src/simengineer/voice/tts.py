"""Synthèse vocale : interface `TTSBackend`, Piper local (CPU) et cache de phrases.

Le spotter utilise toujours un backend local. L'interface permet de brancher plus tard
une voix cloud pour les messages non urgents.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from simengineer.core.log import get_logger

log = get_logger("tts")


@dataclass(frozen=True, slots=True)
class Audio:
    pcm: NDArray[np.int16]  # mono
    sample_rate: int

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / self.sample_rate if self.sample_rate else 0.0


class TTSBackend(Protocol):
    voice_id: str

    def synthesize(self, text: str, lang: str) -> Audio: ...


class TTSUnavailableError(RuntimeError):
    pass


class NullTTS:
    """Aucune voix : silence de durée proportionnelle au texte (tests, mode sans audio)."""

    voice_id = "null"

    def __init__(self, seconds_per_word: float = 0.0) -> None:
        self._spw = seconds_per_word

    def synthesize(self, text: str, lang: str) -> Audio:
        n = int(16000 * self._spw * len(text.split()))
        return Audio(np.zeros(n, dtype=np.int16), 16000)


def _wav_to_audio(data: bytes) -> Audio:
    with wave.open(io.BytesIO(data), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        if wf.getnchannels() > 1:
            pcm = pcm.reshape(-1, wf.getnchannels())[:, 0]
        return Audio(pcm.copy(), wf.getframerate())


class PiperTTS:
    """Piper en local sur CPU. Module Python `piper` si présent, sinon exécutable `piper`.

    L'API exacte du module dépend de la version (hypothèse à vérifier, ASSUMPTIONS E).
    """

    def __init__(self, voices: dict[str, str]) -> None:
        self._paths = {lang: Path(p) for lang, p in voices.items() if p}
        self._voices: dict[str, Any] = {}
        self.voice_id = "piper:" + ",".join(f"{k}={v.stem}" for k, v in self._paths.items())

    def _model(self, lang: str) -> Path:
        path = self._paths.get(lang) or next(iter(self._paths.values()), None)
        if path is None or not path.is_file():
            raise TTSUnavailableError(
                f"Voix Piper introuvable pour {lang!r} : {path}. Voir docs/RUN_ON_PC.md."
            )
        return path

    def synthesize(self, text: str, lang: str) -> Audio:
        model = self._model(lang)
        try:
            from piper import PiperVoice
        except ImportError:
            return self._synthesize_exe(text, model)
        voice = self._voices.get(lang)
        if voice is None:
            voice = self._voices[lang] = PiperVoice.load(str(model))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(text, wf)
            else:
                voice.synthesize(text, wf)
        return _wav_to_audio(buf.getvalue())

    def _synthesize_exe(self, text: str, model: Path) -> Audio:
        exe = shutil.which("piper")
        if exe is None:
            raise TTSUnavailableError("Piper n'est pas installé : pip install simengineer[voice]")
        proc = subprocess.run(
            [exe, "--model", str(model), "--output_file", "-"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=True,
        )
        return _wav_to_audio(proc.stdout)


class CachedTTS:
    """Cache mémoire + disque par (voix, langue, texte). Les phrases fréquentes sont
    pré-synthétisées au démarrage (`prewarm`) : une alerte P0 ne paie jamais la synthèse."""

    def __init__(self, backend: TTSBackend, cache_dir: Path | None, max_memory: int = 512) -> None:
        self._backend = backend
        self._dir = cache_dir
        self._mem: dict[str, Audio] = {}
        self._max = max_memory
        self.voice_id = backend.voice_id
        self.hits = 0
        self.misses = 0
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, text: str, lang: str) -> str:
        raw = f"{self._backend.voice_id}|{lang}|{text}".encode()
        return hashlib.sha1(raw, usedforsecurity=False).hexdigest()

    def is_cached(self, text: str, lang: str) -> bool:
        key = self._key(text, lang)
        return key in self._mem or (self._dir is not None and (self._dir / f"{key}.npz").is_file())

    def synthesize(self, text: str, lang: str) -> Audio:
        key = self._key(text, lang)
        audio = self._mem.get(key)
        if audio is not None:
            self.hits += 1
            return audio
        file = self._dir / f"{key}.npz" if self._dir is not None else None
        if file is not None and file.is_file():
            with np.load(file) as z:
                audio = Audio(z["pcm"], int(z["sr"]))
            self.hits += 1
        else:
            self.misses += 1
            audio = self._backend.synthesize(text, lang)
            if file is not None:
                np.savez(file, pcm=audio.pcm, sr=audio.sample_rate)
        if len(self._mem) >= self._max:
            self._mem.pop(next(iter(self._mem)))
        self._mem[key] = audio
        return audio

    def prewarm(self, phrases: list[str], lang: str) -> int:
        """Pré-synthétise les phrases ; renvoie le nombre de phrases prêtes."""
        ready = 0
        for text in phrases:
            try:
                self.synthesize(text, lang)
                ready += 1
            except TTSUnavailableError as exc:
                log.warning("Pré-synthèse impossible : %s", exc)
                break
        return ready
