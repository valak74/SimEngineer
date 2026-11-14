"""Speakers : relient l'arbitre au TTS et à la sortie audio."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from simengineer.core.log import get_logger
from simengineer.voice.arbiter import Utterance
from simengineer.voice.audio import AudioOutput
from simengineer.voice.tts import TTSBackend, TTSUnavailableError

log = get_logger("speaker")

OnStarted = Callable[[Utterance, float], None]  # (énoncé, latence donnée -> son en ms)


class NullSpeaker:
    """Speaker instantané (mode sans voix, tests) : affiche/enregistre le texte."""

    def __init__(self, echo: Callable[[str], None] | None = None) -> None:
        self.said: list[Utterance] = []
        self.stops = 0
        self._echo = echo
        self.busy_flag = False

    def say(self, utt: Utterance) -> None:
        self.said.append(utt)
        if self._echo:
            self._echo(f"[P{int(utt.priority)}] {utt.text}")

    def stop(self) -> None:
        self.stops += 1
        self.busy_flag = False

    def busy(self) -> bool:
        return self.busy_flag


class TTSSpeaker:
    """Synthèse (avec cache) et lecture dans un thread dédié, interruptible."""

    def __init__(
        self,
        tts: TTSBackend,
        audio: AudioOutput,
        lang: str = "fr",
        on_started: OnStarted | None = None,
    ) -> None:
        self._tts = tts
        self._audio = audio
        self._lang = lang
        self._on_started = on_started
        self._q: queue.Queue[Utterance | None] = queue.Queue()
        self._busy = threading.Event()
        self._generation = 0
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()

    def say(self, utt: Utterance) -> None:
        self._busy.set()
        self._q.put(utt)

    def stop(self) -> None:
        self._generation += 1  # invalide ce qui est en cours de synthèse
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._audio.stop()

    def busy(self) -> bool:
        return self._busy.is_set()

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2.0)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while self._busy.is_set() and time.monotonic() < end:
            time.sleep(0.005)
        return not self._busy.is_set()

    def _run(self) -> None:
        while True:
            utt = self._q.get()
            if utt is None:
                return
            generation = self._generation
            try:
                audio = self._tts.synthesize(utt.text, self._lang)
                if generation == self._generation:
                    if self._on_started and utt.data_time is not None:
                        self._on_started(utt, (time.perf_counter() - utt.data_time) * 1000.0)
                    self._audio.play(audio)
            except TTSUnavailableError as exc:
                log.error("Voix indisponible (%s) : %s", exc, utt.text)
            except Exception:
                log.exception("Erreur de lecture audio")
            finally:
                if self._q.empty():
                    self._busy.clear()
