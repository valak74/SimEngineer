"""Commandes de stand : SEULES écritures vers le simulateur (règle 1).

Passent par les commandes broadcast officielles du SDK, uniquement si
`engineer.pit_commands_enabled = true` (désactivé par défaut).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from simengineer.core.log import get_logger

log = get_logger("pit")


@dataclass(frozen=True, slots=True)
class PitResult:
    ok: bool
    key: str  # gabarit de confirmation ou de refus
    params: dict[str, Any] = field(default_factory=dict)


class PitCommander(Protocol):
    def set_fuel(self, liters: float) -> PitResult: ...
    def set_tyres(self, change: bool) -> PitResult: ...


class DisabledPitCommander:
    """Par défaut : aucune écriture, réponse explicite."""

    def set_fuel(self, liters: float) -> PitResult:
        return PitResult(False, "pit_cmd_disabled")

    def set_tyres(self, change: bool) -> PitResult:
        return PitResult(False, "pit_cmd_disabled")


class RecordingPitCommander:
    """Sources synthétique / replay : enregistre les commandes sans rien envoyer."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, float]] = []

    def set_fuel(self, liters: float) -> PitResult:
        amount = max(0, math.ceil(liters))
        self.commands.append(("fuel", float(amount)))
        return PitResult(True, "pit_cmd_fuel", {"liters": amount})

    def set_tyres(self, change: bool) -> PitResult:
        self.commands.append(("tyres", float(change)))
        return PitResult(True, "pit_cmd_tyres_on" if change else "pit_cmd_tyres_off")


class IrsdkPitCommander:
    """Commandes broadcast pyirsdk (API à vérifier, docs/ASSUMPTIONS.md section C)."""

    def __init__(self, sdk: Any) -> None:
        import irsdk

        self._ir = sdk
        self._mode = irsdk.PitCommandMode

    def set_fuel(self, liters: float) -> PitResult:
        amount = max(0, math.ceil(liters))
        log.info("Commande stand : carburant %d L", amount)
        self._ir.pit_command(self._mode.fuel, amount)
        return PitResult(True, "pit_cmd_fuel", {"liters": amount})

    def set_tyres(self, change: bool) -> PitResult:
        log.info("Commande stand : pneus %s", "oui" if change else "non")
        if change:
            for mode in (self._mode.lf, self._mode.rf, self._mode.lr, self._mode.rr):
                self._ir.pit_command(mode)
            return PitResult(True, "pit_cmd_tyres_on")
        self._ir.pit_command(self._mode.clear_tires)
        return PitResult(True, "pit_cmd_tyres_off")


def make_pit_commander(enabled: bool, source: Any) -> PitCommander:
    if not enabled:
        return DisabledPitCommander()
    sdk = getattr(source, "sdk", None)
    if sdk is not None:
        return IrsdkPitCommander(sdk)
    return RecordingPitCommander()
