"""Arbitre de parole unique : une seule voix, quatre priorités.

P0 danger (coupe tout) > P1 course > P2 coaching (ligne droite uniquement, jamais en
freinage ni en duel) > P3 conversation. Anti-répétition par clé, expiration des messages
périmés, limite de 8 mots pour P0/P2. Aucune E/S ici : le `Speaker` est injecté.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol

from simengineer.core.log import get_logger
from simengineer.voice.templates import MAX_WORDS_SHORT, word_count

log = get_logger("arbiter")


class Priority(IntEnum):
    P0 = 0  # danger
    P1 = 1  # course
    P2 = 2  # coaching
    P3 = 3  # conversation


DEFAULT_TTL_S = {Priority.P0: 2.0, Priority.P1: 20.0, Priority.P2: 45.0, Priority.P3: 30.0}


@dataclass(slots=True)
class Utterance:
    priority: Priority
    key: str  # clé d'anti-répétition
    text: str
    ttl_s: float | None = None  # None = valeur par défaut de la priorité
    cooldown_s: float = 0.0  # la même clé n'est pas redite avant ce délai
    data_time: float | None = None  # perf_counter de l'image à l'origine (mesure de latence)
    created: float = field(default=0.0)


@dataclass(slots=True)
class SpeechContext:
    """État de pilotage utilisé pour retenir le coaching."""

    on_straight: bool = True
    braking: bool = False
    in_battle: bool = False

    @property
    def coaching_ok(self) -> bool:
        return self.on_straight and not self.braking and not self.in_battle


class Speaker(Protocol):
    def say(self, utt: Utterance) -> None: ...
    def stop(self) -> None: ...
    def busy(self) -> bool: ...


class SpeechArbiter:
    def __init__(self, speaker: Speaker, clock: Callable[[], float] = time.monotonic) -> None:
        self._speaker = speaker
        self._clock = clock
        self._queue: list[Utterance] = []
        self._last_spoken: dict[str, float] = {}
        self._current: Utterance | None = None
        self.spoken: list[Utterance] = []  # historique (tests, rapport)
        self.dropped = 0

    # --- soumission
    def submit(self, utt: Utterance) -> bool:
        """Propose un énoncé. Renvoie False s'il est refusé (répétition, trop long)."""
        now = self._clock()
        utt.created = now
        if utt.ttl_s is None:
            utt.ttl_s = DEFAULT_TTL_S[utt.priority]
        last = self._last_spoken.get(utt.key)
        if last is not None and now - last < utt.cooldown_s:
            self.dropped += 1
            return False
        if utt.priority in (Priority.P0, Priority.P2) and word_count(utt.text) > MAX_WORDS_SHORT:
            if utt.priority is Priority.P2:
                log.warning("Conseil trop long refusé : %r", utt.text)
                self.dropped += 1
                return False
            log.error("Alerte P0 de plus de %d mots : %r", MAX_WORDS_SHORT, utt.text)
        # Une même clé en attente est remplacée par la version la plus récente.
        self._queue = [q for q in self._queue if q.key != utt.key]
        if utt.priority is Priority.P0:
            return self._submit_danger(utt)
        self._queue.append(utt)
        return True

    def _submit_danger(self, utt: Utterance) -> bool:
        cur = self._current if self._speaker.busy() else None
        if cur is not None and cur.priority is Priority.P0:
            self._queue.append(utt)  # deux dangers : dans l'ordre, sans se couper
            return True
        if cur is not None:
            self._speaker.stop()
            if cur.priority is Priority.P1:  # l'info de course coupée sera redite si valide
                self._last_spoken.pop(cur.key, None)
                self._queue.append(cur)
        self._speak(utt)
        return True

    # --- boucle
    def tick(self, ctx: SpeechContext | None = None) -> Utterance | None:
        """À appeler régulièrement : parle le prochain énoncé éligible, s'il y en a un."""
        if not self._queue:
            return None
        now = self._clock()
        alive = [q for q in self._queue if now - q.created <= (q.ttl_s or 0.0)]
        self.dropped += len(self._queue) - len(alive)
        self._queue = alive
        if not alive or self._speaker.busy():
            return None
        coaching_ok = ctx is None or ctx.coaching_ok
        best: Utterance | None = None
        for q in alive:
            if q.priority is Priority.P2 and not coaching_ok:
                continue
            if best is None or q.priority < best.priority:
                best = q
        if best is None:
            return None
        self._queue.remove(best)
        self._speak(best)
        return best

    def _speak(self, utt: Utterance) -> None:
        self._current = utt
        self._last_spoken[utt.key] = self._clock()
        self.spoken.append(utt)
        self._speaker.say(utt)

    def pending(self) -> int:
        return len(self._queue)

    def clear(self, max_priority: Priority = Priority.P1) -> None:
        """Vide les messages de priorité inférieure (ex. fin de session)."""
        self._queue = [q for q in self._queue if q.priority <= max_priority]
