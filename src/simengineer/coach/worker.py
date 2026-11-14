"""Exécution des analyses lourdes hors de la boucle temps réel (règle 4).

`ProcessWorker` : un processus séparé à basse priorité. `InlineWorker` : exécution
immédiate (tests, rejeu accéléré).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from multiprocessing import get_context
from typing import Any, Protocol

from simengineer.core.log import get_logger

log = get_logger("worker")


def lower_priority() -> None:
    """Passe le processus courant en basse priorité (Windows : IDLE_PRIORITY_CLASS)."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000040)
        else:
            os.nice(10)
    except Exception:  # la priorité est un confort, jamais une raison d'échouer
        log.debug("Impossible de baisser la priorité du processus", exc_info=True)


class Worker(Protocol):
    def submit(self, fn: Callable[..., Any], *args: Any) -> None: ...
    def poll(self) -> list[Any]: ...
    def drain(self, timeout_s: float = 30.0) -> list[Any]: ...
    def close(self) -> None: ...


class InlineWorker:
    def __init__(self) -> None:
        self._done: list[Any] = []

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        self._done.append(fn(*args))

    def poll(self) -> list[Any]:
        done, self._done = self._done, []
        return done

    def drain(self, timeout_s: float = 30.0) -> list[Any]:
        return self.poll()

    def close(self) -> None:
        return None


class ProcessWorker:
    def __init__(self) -> None:
        self._pool = ProcessPoolExecutor(
            max_workers=1, mp_context=get_context("spawn"), initializer=lower_priority
        )
        self._pending: list[Future[Any]] = []

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        self._pending.append(self._pool.submit(fn, *args))

    def _collect(self, futures: list[Future[Any]]) -> list[Any]:
        out = []
        for fut in futures:
            try:
                out.append(fut.result())
            except Exception:
                log.exception("Échec d'une analyse en arrière-plan")
        return out

    def poll(self) -> list[Any]:
        if not self._pending:
            return []
        done = [f for f in self._pending if f.done()]
        self._pending = [f for f in self._pending if not f.done()]
        return self._collect(done)

    def drain(self, timeout_s: float = 30.0) -> list[Any]:
        from concurrent.futures import wait

        wait(self._pending, timeout=timeout_s)
        return self.poll()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
