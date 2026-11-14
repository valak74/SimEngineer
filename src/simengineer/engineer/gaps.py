"""Écarts avec les voitures les plus proches en piste (position physique, pas le classement).

Estimation : écart en distance le long de la piste converti en temps avec le temps au tour
de référence du pilote. iRacing ne fournit pas mieux pour les autres voitures.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simengineer.core.types import Frame
from simengineer.core.vars import TrackSurface


@dataclass(frozen=True, slots=True)
class Gaps:
    ahead_s: float | None = None
    behind_s: float | None = None
    ahead_idx: int | None = None
    behind_idx: int | None = None


def compute_gaps(f: Frame, lap_time_s: float | None) -> Gaps:
    pcts, surfaces = f.car_idx_lap_dist_pct, f.car_idx_track_surface
    if pcts is None or surfaces is None or not lap_time_s:
        return Gaps()
    mask = (surfaces == TrackSurface.ON_TRACK) & (pcts >= 0.0)
    if 0 <= f.player_car_idx < len(mask):
        mask[f.player_car_idx] = False
    idxs = np.flatnonzero(mask)
    if idxs.size == 0:
        return Gaps()
    fwd = (pcts[idxs] - f.lap_dist_pct) % 1.0
    ia, ib = int(np.argmin(fwd)), int(np.argmax(fwd))
    return Gaps(
        ahead_s=float(fwd[ia]) * lap_time_s,
        behind_s=float(1.0 - fwd[ib]) * lap_time_s,
        ahead_idx=int(idxs[ia]),
        behind_idx=int(idxs[ib]),
    )
