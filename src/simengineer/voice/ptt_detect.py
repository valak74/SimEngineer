"""Détection du bouton push-to-talk : le pilote appuie, on enregistre ce qui a bougé."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Detected:
    kind: str  # "joystick" | "keyboard"
    joystick_index: int = 0
    button: int = 0
    key: str = ""
    device: str = ""


# Une sonde renvoie un `Detected` dès qu'elle voit un appui, sinon None.
Probe = Callable[[], Detected | None]


def detect(probes: list[Probe], timeout_s: float = 20.0, poll_s: float = 0.02) -> Detected | None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        for probe in probes:
            hit = probe()
            if hit is not None:
                return hit
        time.sleep(poll_s)
    return None


def joystick_probe() -> Probe | None:
    """Sonde volants / boîtes à boutons (pygame). None si indisponible."""
    try:
        import os

        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
    except ImportError:
        return None
    pygame.init()
    pygame.joystick.init()
    sticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for s in sticks:
        s.init()
    pygame.event.pump()
    baseline = {
        i: [s.get_button(b) for b in range(s.get_numbuttons())] for i, s in enumerate(sticks)
    }

    def probe() -> Detected | None:
        pygame.event.pump()
        for i, s in enumerate(sticks):
            for b in range(s.get_numbuttons()):
                if s.get_button(b) and not baseline[i][b]:
                    return Detected("joystick", i, b, device=str(s.get_name()))
        return None

    return probe if sticks else None


def keyboard_probe() -> Probe | None:
    """Sonde clavier (module keyboard, Windows). None si indisponible."""
    try:
        import keyboard
    except ImportError:
        return None
    pressed: list[str] = []

    def on_event(ev: Any) -> None:
        if ev.event_type == "down" and ev.name and ev.name not in ("enter", "esc"):
            pressed.append(str(ev.name))

    try:
        keyboard.hook(on_event)
    except Exception:  # droits insuffisants, pas de clavier…
        return None

    def probe() -> Detected | None:
        if pressed:
            keyboard.unhook_all()
            return Detected("keyboard", key=pressed[0])
        return None

    return probe
