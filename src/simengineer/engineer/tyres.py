"""Suivi des pneus. iRacing ne rafraîchit usure/températures qu'aux stands (hypothèse) :
entre deux arrêts, l'usure est extrapolée linéairement à partir du relais précédent."""

from __future__ import annotations

from dataclasses import dataclass, field

CORNERS = ("lf", "rf", "lr", "rr")


@dataclass(frozen=True, slots=True)
class TyreStatus:
    stint_laps: int
    wear_pct: float | None = None  # usure restante estimée du pneu le plus usé (0..100)
    wear_per_lap_pct: float | None = None
    laps_to_threshold: float | None = None
    temps_c: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    pressures_kpa: dict[str, float] = field(default_factory=dict)


def _min_wear(slow: dict[str, float]) -> float | None:
    vals = [
        slow[k]
        for c in CORNERS
        for p in ("l", "m", "r")
        if (k := f"tyre_{c}_wear_{p}") in slow and slow[k] == slow[k]
    ]
    return min(vals) * 100.0 if vals else None


class TyreModel:
    def __init__(self, wear_warning_pct: float = 35.0) -> None:
        self.warning_pct = wear_warning_pct
        self.stint_laps = 0
        self._snapshot: float | None = None  # usure lue (dernière mise à jour iRacing)
        self._snapshot_stint_laps = 0  # tours du relais au moment de la lecture
        self._rate: float | None = None
        self._temps: dict[str, tuple[float, float, float]] = {}
        self._press: dict[str, float] = {}

    def on_slow(self, slow: dict[str, float]) -> None:
        wear = _min_wear(slow)
        if wear is not None and wear != self._snapshot:
            if self._snapshot is not None and wear < self._snapshot and self.stint_laps > 0:
                self._rate = (self._snapshot - wear) / self.stint_laps
            if self._snapshot is not None and wear > self._snapshot:
                self.stint_laps = 0  # pneus neufs
            self._snapshot = wear
            self._snapshot_stint_laps = self.stint_laps
        for c in CORNERS:
            keys = [f"tyre_{c}_temp_{p}" for p in ("cl", "cm", "cr")]
            if all(k in slow for k in keys):
                self._temps[c] = (slow[keys[0]], slow[keys[1]], slow[keys[2]])
            if f"tyre_{c}_pressure" in slow:
                self._press[c] = slow[f"tyre_{c}_pressure"]

    def on_lap(self, pit: bool) -> None:
        self.stint_laps += 1
        if pit and self._rate is None:
            self._snapshot_stint_laps = self.stint_laps

    def status(self) -> TyreStatus:
        wear = self._snapshot
        to_threshold = None
        if wear is not None and self._rate:
            wear = max(wear - self._rate * (self.stint_laps - self._snapshot_stint_laps), 0.0)
            to_threshold = max(wear - self.warning_pct, 0.0) / self._rate
        return TyreStatus(
            self.stint_laps, wear, self._rate, to_threshold, dict(self._temps), dict(self._press)
        )
