"""Modèle carburant : moyenne glissante sur tours propres, marge configurable.

Tous les chiffres sont calculés ici (règle 3) ; le LLM ne fait que les lire.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from simengineer.core.types import Frame, SessionInfo
from simengineer.telemetry.laps import Lap

_UNLIMITED_TIME_S = 600_000.0  # au-delà : pas de limite de temps réelle


@dataclass(frozen=True, slots=True)
class FuelStatus:
    fuel_l: float
    clean_laps: int
    avg_per_lap_l: float | None = None
    laps_in_tank: float | None = None  # tours faisables avec le carburant actuel (hors réserve)
    laps_remaining: float | None = None  # tours de course restants (None hors course)
    fuel_needed_l: float | None = None  # pour finir, marge comprise
    fuel_to_add_l: float | None = None
    margin_laps: float | None = None  # tours d'avance (+) ou de retard (-) à l'arrivée
    can_finish: bool | None = None


class FuelModel:
    def __init__(self, window: int = 5, margin_laps: float = 1.0, reserve_l: float = 0.3) -> None:
        self.margin_laps = margin_laps
        self.reserve_l = reserve_l
        self._used: deque[float] = deque(maxlen=window)
        self._times: deque[float] = deque(maxlen=window)

    def add_lap(self, lap: Lap) -> bool:
        """Ajoute un tour à la moyenne s'il est représentatif. Renvoie True si retenu."""
        used = lap.fuel_used
        if not lap.clean_for_fuel or not math.isfinite(used) or used <= 0.0:
            return False
        self._used.append(used)
        self._times.append(lap.lap_time)
        return True

    @property
    def clean_laps(self) -> int:
        return len(self._used)

    @property
    def avg_per_lap(self) -> float | None:
        return sum(self._used) / len(self._used) if self._used else None

    @property
    def avg_lap_time(self) -> float | None:
        return sum(self._times) / len(self._times) if self._times else None

    def status(self, fuel_l: float, laps_remaining: float | None) -> FuelStatus:
        avg = self.avg_per_lap
        if avg is None or not math.isfinite(fuel_l):
            return FuelStatus(fuel_l, self.clean_laps, laps_remaining=laps_remaining)
        in_tank = max(fuel_l - self.reserve_l, 0.0) / avg
        if laps_remaining is None:
            return FuelStatus(fuel_l, self.clean_laps, avg, in_tank)
        needed = (laps_remaining + self.margin_laps) * avg + self.reserve_l
        to_add = max(needed - fuel_l, 0.0)
        return FuelStatus(
            fuel_l=fuel_l,
            clean_laps=self.clean_laps,
            avg_per_lap_l=avg,
            laps_in_tank=in_tank,
            laps_remaining=laps_remaining,
            fuel_needed_l=needed,
            fuel_to_add_l=to_add,
            margin_laps=in_tank - laps_remaining,
            can_finish=to_add <= 0.0,
        )


def race_laps_remaining(
    f: Frame, info: SessionInfo, avg_lap_time: float | None, timed_extra_laps: int = 1
) -> float | None:
    """Tours restant à couvrir à partir de maintenant (fractionnaire), None hors course.

    Course en tours : `session_laps_remain` compte le tour en cours (hypothèse à vérifier).
    Course au temps : on finit le tour en cours, puis chaque tour commencé avant la fin du
    chrono, plus `timed_extra_laps` (règle du tour supplémentaire, dépend du leader).
    """
    if not info.is_race:
        return None
    to_line = 1.0 - f.lap_dist_pct
    if f.session_laps_remain >= 0:
        return max(f.session_laps_remain - f.lap_dist_pct, 0.0)
    remain = f.session_time_remain
    if not math.isfinite(remain) or remain >= _UNLIMITED_TIME_S or avg_lap_time is None:
        return None
    after_line = max(remain - to_line * avg_lap_time, 0.0)
    return to_line + math.ceil(after_line / avg_lap_time) + (timed_extra_laps if remain > 0 else 0)
