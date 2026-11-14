"""Delta temps réel par rapport au tour de référence : O(1) par image."""

from __future__ import annotations

import numpy as np

from simengineer.core.types import Frame
from simengineer.telemetry.resample import GridLap


class LiveDelta:
    def __init__(self, ref: GridLap) -> None:
        # Temps de référence à chaque mètre, + temps au tour pour interpoler jusqu'à la ligne.
        self._t = np.concatenate((ref.ch("time"), [ref.lap_time])).tolist()
        self._n = ref.n
        self._lap_start: float | None = None
        self._prev_pct = -1.0
        self._prev_time = 0.0
        self.delta_s: float | None = None

    def update(self, f: Frame) -> float | None:
        """Renvoie le delta courant (+ = plus lent que la référence), None s'il est inconnu."""
        pct, now = f.lap_dist_pct, f.session_time
        if self._prev_pct > 0.85 and pct < 0.15:
            before = 1.0 - self._prev_pct
            span = before + pct
            self._lap_start = self._prev_time + (now - self._prev_time) * (
                before / span if span > 0 else 0.0
            )
        elif self._prev_pct < 0 and pct * self._n < 5.0 and f.speed > 5.0:
            self._lap_start = now - pct * self._n / f.speed  # départ lancé sur la ligne
        elif abs(pct - self._prev_pct) > 0.02 and self._prev_pct >= 0:
            self._lap_start = None  # téléportation : delta inconnu jusqu'à la prochaine ligne
        self._prev_pct, self._prev_time = pct, now
        if self._lap_start is None or f.on_pit_road:
            self.delta_s = None
            return None
        x = pct * self._n
        i = min(int(x), self._n - 1)
        ref_t = self._t[i] + (self._t[i + 1] - self._t[i]) * (x - i)
        self.delta_s = (now - self._lap_start) - ref_t
        return self.delta_s
