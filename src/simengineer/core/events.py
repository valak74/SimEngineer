"""Bus d'événements synchrone minimal (un seul thread : la boucle principale)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from simengineer.core.types import SessionInfo
from simengineer.telemetry.laps import Lap

E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class SessionStarted:
    info: SessionInfo
    session_id: int


@dataclass(frozen=True, slots=True)
class LapCompleted:
    lap: Lap
    lap_id: int


@dataclass(frozen=True, slots=True)
class SessionEnded:
    session_id: int


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: object) -> None:
        for handler in self._handlers.get(type(event), ()):
            handler(event)
