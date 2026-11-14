"""Source `live` : mémoire partagée iRacing via pyirsdk (Windows uniquement, lecture seule).

pyirsdk est importé paresseusement. Rien ici n'écrit dans le simulateur.
L'API pyirsdk utilisée est une hypothèse à vérifier (docs/ASSUMPTIONS.md, section C).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from simengineer.core.log import get_logger
from simengineer.core.types import Frame, SessionInfo, VarDescription
from simengineer.core.vars import VARS
from simengineer.sources.base import TICK_RATE, FrameBuilder
from simengineer.sources.session_yaml import session_info_from_dicts

log = get_logger("live")

_TYPE_NAMES = {0: "char", 1: "bool", 2: "int", 3: "bitfield", 4: "float", 5: "double"}


class SimNotRunningError(RuntimeError):
    pass


def _load_irsdk() -> Any:
    try:
        import irsdk
    except ImportError as exc:  # pragma: no cover - dépend de la plateforme
        raise SimNotRunningError(
            "pyirsdk n'est pas installé (Windows uniquement) : pip install simengineer[live]"
        ) from exc
    return irsdk


class LiveSource:
    name = "live"

    def __init__(self, wait_for_sim: bool = True, poll_s: float = 2.0) -> None:
        self._wait = wait_for_sim
        self._poll_s = poll_s
        self._ir: Any = None

    @property
    def sdk(self) -> Any:
        """Objet pyirsdk (utilisé uniquement par les commandes de stand, si activées)."""
        return self._ir

    def open(self) -> None:
        irsdk = _load_irsdk()
        self._ir = irsdk.IRSDK()
        announced = False
        while not (self._ir.startup() and self._ir.is_initialized and self._ir.is_connected):
            if not self._wait:
                raise SimNotRunningError("iRacing n'est pas lancé (ou pas encore en piste).")
            if not announced:
                log.info("En attente d'iRacing…")
                announced = True
            time.sleep(self._poll_s)  # attente quasi gratuite en CPU
        log.info("Connecté à iRacing.")

    def close(self) -> None:
        if self._ir is not None:
            self._ir.shutdown()
            self._ir = None

    def session_info(self) -> SessionInfo:
        ir = self._ir
        num_name = VARS["session_num"].name
        num = int(ir[num_name] or 0) if num_name in self._names() else 0
        return session_info_from_dicts(
            ir["WeekendInfo"] or {}, ir["DriverInfo"] or {}, ir["SessionInfo"] or {}, num
        )

    def _names(self) -> list[str]:
        return list(self._ir.var_headers_names or [])

    def available_vars(self) -> list[VarDescription]:
        out = []
        headers = getattr(self._ir, "_var_headers", None) or []
        if headers:
            for h in headers:
                out.append(
                    VarDescription(
                        h.name, _TYPE_NAMES.get(h.type, "?"), int(h.count), h.unit, h.desc
                    )
                )
        else:
            out = [VarDescription(n, "?", 1, "", "") for n in self._names()]
        return sorted(out, key=lambda d: d.name)

    def frames(self) -> Iterator[Frame]:
        ir = self._ir
        builder = FrameBuilder(self._names())
        period = 1.0 / TICK_RATE
        get = ir.__getitem__
        tick = 0
        last_time = -1.0
        next_at = time.perf_counter()
        while ir.is_initialized and ir.is_connected:
            ir.freeze_var_buffer_latest()
            frame = builder.build(tick, get, with_slow=(tick % TICK_RATE == 0))
            ir.unfreeze_var_buffer_latest()
            if frame.session_time != last_time:  # ignore les images dupliquées (pause, menu)
                last_time = frame.session_time
                tick += 1
                yield frame
            next_at += period
            delay = next_at - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_at = time.perf_counter()
        log.info("iRacing déconnecté.")
