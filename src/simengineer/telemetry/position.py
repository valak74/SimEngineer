"""Mesure de la précision de position (Lat/Lon du SDK) : à mesurer, pas à promettre.

Compare la trajectoire de plusieurs tours propres point par point (grille 1 m). La
dispersion mesurée mélange la régularité du pilote et la précision du SDK : c'est donc
une borne supérieure de l'erreur de position.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simengineer.core.types import FloatArray
from simengineer.telemetry.resample import GridLap

EARTH_M_PER_DEG = 111_320.0


@dataclass(frozen=True, slots=True)
class PositionPrecision:
    laps: int
    available: bool
    quantization_m: float | None = None  # plus petit pas de position observé
    median_spread_m: float | None = None  # dispersion médiane tour à tour
    p95_spread_m: float | None = None
    max_spread_m: float | None = None


def _xy(grid: GridLap, lat0: float, lon0: float) -> tuple[FloatArray, FloatArray]:
    x = (grid.ch("lon") - lon0) * EARTH_M_PER_DEG * np.cos(np.radians(lat0))
    y = (grid.ch("lat") - lat0) * EARTH_M_PER_DEG
    return x, y


def measure_position_precision(grids: list[GridLap]) -> PositionPrecision:
    usable = [g for g in grids if g.has("lat") and g.has("lon")]
    if len(usable) < 2:
        return PositionPrecision(len(usable), False)
    n = min(g.n for g in usable)
    lat0 = float(np.mean(usable[0].ch("lat")))
    lon0 = float(np.mean(usable[0].ch("lon")))
    xs, ys = zip(*(_xy(g, lat0, lon0) for g in usable), strict=True)
    x = np.stack([a[:n] for a in xs])
    y = np.stack([a[:n] for a in ys])
    spread = np.sqrt(x.var(axis=0) + y.var(axis=0))  # écart-type 2D par point de la grille
    steps = np.abs(np.diff(usable[0].ch("lat"))) * EARTH_M_PER_DEG
    steps = steps[steps > 0]
    return PositionPrecision(
        laps=len(usable),
        available=True,
        quantization_m=float(steps.min()) if steps.size else None,
        median_spread_m=float(np.median(spread)),
        p95_spread_m=float(np.percentile(spread, 95)),
        max_spread_m=float(spread.max()),
    )
