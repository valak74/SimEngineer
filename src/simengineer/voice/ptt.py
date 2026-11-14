"""Push-to-talk : bouton (clavier, volant, boîte à boutons) + capture micro pendant l'appui."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from simengineer.core.config import VoiceConfig
from simengineer.core.log import get_logger
from simengineer.voice.stt import STT_SAMPLE_RATE, STTBackend

log = get_logger("ptt")


class PTTButton(Protocol):
    def pressed(self) -> bool: ...


class PTTUnavailableError(RuntimeError):
    pass


class KeyboardButton:
    def __init__(self, key: str) -> None:
        try:
            import keyboard
        except ImportError as exc:
            raise PTTUnavailableError(
                "module keyboard absent : pip install simengineer[voice]"
            ) from exc
        self._kb = keyboard
        self._key = key

    def pressed(self) -> bool:
        return bool(self._kb.is_pressed(self._key))


class JoystickButton:
    """Bouton de volant ou de boîte à boutons (pygame, sans fenêtre)."""

    def __init__(self, joystick_index: int, button: int) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise PTTUnavailableError("pygame absent : pip install simengineer[voice]") from exc
        self._pg = pygame
        pygame.init()
        pygame.joystick.init()
        if joystick_index >= pygame.joystick.get_count():
            raise PTTUnavailableError(f"Contrôleur {joystick_index} introuvable")
        self._joy = pygame.joystick.Joystick(joystick_index)
        self._joy.init()
        self._button = button

    def pressed(self) -> bool:
        self._pg.event.pump()
        return bool(self._joy.get_button(self._button))


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> NDArray[np.int16]: ...


class MicRecorder:
    """Capture micro 16 kHz mono, ouverte uniquement pendant l'appui."""

    def __init__(self, device: str = "") -> None:
        from simengineer.voice.audio import _sd, list_devices, resolve_device

        self._sd = _sd()
        self._device = resolve_device(device, list_devices(), "inputs") if device else None
        self._chunks: list[NDArray[np.int16]] = []
        self._stream: Any = None

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        self._chunks.append(indata[:, 0].copy())

    def start(self) -> None:
        self._chunks = []
        self._stream = self._sd.InputStream(
            samplerate=STT_SAMPLE_RATE, channels=1, dtype="int16", device=self._device,
            callback=self._callback,
        )  # fmt: skip
        self._stream.start()

    def stop(self) -> NDArray[np.int16]:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(self._chunks)


class PushToTalk:
    """Surveille le bouton (50 Hz), enregistre pendant l'appui, transcrit au relâchement."""

    def __init__(
        self,
        button: PTTButton,
        recorder: Recorder,
        stt: STTBackend,
        on_text: Callable[[str], None],
        lang: str = "fr",
        max_record_s: float = 12.0,
        poll_s: float = 0.02,
        on_press: Callable[[], None] | None = None,
    ) -> None:
        self._button = button
        self._recorder = recorder
        self._stt = stt
        self._on_text = on_text
        self._lang = lang
        self._max = max_record_s
        self._poll = poll_s
        self._on_press = on_press
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ptt", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._button.pressed():
                    self._capture()
            except Exception:
                log.exception("Erreur push-to-talk")
                time.sleep(1.0)
            time.sleep(self._poll)

    def _capture(self) -> None:
        if self._on_press:
            self._on_press()
        self._recorder.start()
        start = time.monotonic()
        while (
            self._button.pressed()
            and not self._stop.is_set()
            and time.monotonic() - start < self._max
        ):
            time.sleep(self._poll)
        pcm = self._recorder.stop()
        if len(pcm) < STT_SAMPLE_RATE * 0.3:  # appui trop bref : ignoré
            return
        text = self._stt.transcribe(pcm, STT_SAMPLE_RATE, self._lang).strip()
        log.info("Question : %s", text)
        if text:
            self._on_text(text)
        while self._button.pressed() and not self._stop.is_set():
            time.sleep(self._poll)


def make_button(cfg: VoiceConfig) -> PTTButton | None:
    if cfg.ptt_kind == "keyboard":
        return KeyboardButton(cfg.ptt_key)
    if cfg.ptt_kind == "joystick":
        return JoystickButton(cfg.ptt_joystick_index, cfg.ptt_button)
    return None
