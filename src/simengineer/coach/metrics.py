"""Métriques par virage sur un tour rééchantillonné (grille 1 m)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from simengineer.coach.corners import Corner, smooth
from simengineer.telemetry.resample import GridLap

BRAKE_ON = 0.10
THROTTLE_ON = 0.25
THROTTLE_FULL = 0.95
CORRECTION_RAD = 0.03


@dataclass(frozen=True, slots=True)
class CornerMetrics:
    corner: int
    time_s: float  # temps passé dans la zone du virage
    entry_speed: float  # m/s au point de freinage (ou à l'entrée s'il n'y a pas de freinage)
    min_speed: float
    exit_speed: float
    apex_m: float  # distance du point de vitesse minimale
    brake_point_m: float | None = None
    brake_peak: float = 0.0
    brake_rise_s: float | None = None  # durée de montée de 10 % à 90 % de la pression max
    trail_m: float | None = None  # distance de relâchement de 80 % à 10 % de la pression max
    brake_release_m: float | None = None
    throttle_pickup_m: float | None = None
    full_throttle_m: float | None = None
    steering_corrections: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first(mask: np.ndarray[Any, Any], offset: int = 0) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[0]) + offset if idx.size else None


def count_corrections(steering: np.ndarray[Any, Any], step: float) -> int:
    """Corrections de volant : alternances du résidu (volant - volant lissé sur ~30 m)."""
    if len(steering) < 5:
        return 0
    window = max(3, int(30.0 / step))
    pad = np.pad(steering, window, mode="edge")
    trend = np.convolve(pad, np.ones(window) / window, mode="same")[window:-window]
    residual = steering - trend
    signs = np.sign(residual[np.abs(residual) > CORRECTION_RAD])
    if signs.size == 0:
        return 0
    return int(np.count_nonzero(np.diff(signs))) // 2


def corner_metrics(grid: GridLap, c: Corner) -> CornerMetrics:
    step = grid.step
    n = grid.n
    z0 = min(int(c.zone_start_m / step), n - 1)
    z1 = min(int(c.zone_end_m / step), n)
    s0 = min(int(c.start_m / step), n - 1)
    s1 = min(int(c.end_m / step), n - 1)
    t = grid.ch("time")
    speed, brake, throttle = grid.ch("speed"), grid.ch("brake"), grid.ch("throttle")
    t_end = grid.lap_time if z1 >= n else float(t[z1])
    time_s = t_end - float(t[z0])

    # Point de vitesse minimale : cherché autour du virage (le pilote peut décaler sa corde).
    lo, hi = max(z0, s0 - 30), min(z1, s1 + 30)
    apex = lo + int(np.argmin(speed[lo:hi])) if hi > lo else s0
    min_speed = float(speed[apex])
    exit_speed = float(speed[s1])

    # Freinage : dernier épisode de frein commencé avant le point de vitesse minimale.
    bp: int | None = None
    release: int | None = None
    on = brake[z0 : apex + 1] > BRAKE_ON
    peak, rise, trail = 0.0, None, None
    if on.any():
        edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8)))) == 1)
        bp = z0 + int(edges[-1])
        after = np.flatnonzero(brake[bp:z1] <= BRAKE_ON)
        release = bp + int(after[0]) if after.size else z1 - 1
        seg = brake[bp : release + 1]
        peak = float(seg.max())
        i10 = _first(seg >= 0.1 * peak)
        i90 = _first(seg >= 0.9 * peak)
        if i10 is not None and i90 is not None:
            rise = float(t[bp + i90] - t[bp + i10])
        high = np.flatnonzero(seg >= 0.8 * peak)
        low = np.flatnonzero(seg <= max(0.1 * peak, BRAKE_ON))
        low = low[low > high[-1]] if high.size else low
        if high.size:
            end = int(low[0]) if low.size else len(seg) - 1
            trail = float((end - int(high[-1])) * step)
    entry_speed = float(speed[bp]) if bp is not None else float(speed[s0])

    # Reprise des gaz : premier point après la vitesse mini où les gaz restent ouverts.
    pickup = _first(throttle[apex:z1] >= THROTTLE_ON, apex)
    if bp is None:
        # Virage sans freinage : reprise après le dernier lever de pied avant la corde.
        lifts = np.flatnonzero(throttle[z0 : apex + 1] < THROTTLE_ON)
        pickup = None if lifts.size == 0 else pickup
    full = _first(throttle[pickup:z1] >= THROTTLE_FULL, pickup) if pickup is not None else None
    steer = smooth(grid.ch("steering"), 3)[s0 : s1 + 1]
    return CornerMetrics(
        corner=c.number,
        time_s=time_s,
        entry_speed=entry_speed,
        min_speed=min_speed,
        exit_speed=exit_speed,
        apex_m=apex * step,
        brake_point_m=bp * step if bp is not None else None,
        brake_peak=peak,
        brake_rise_s=rise,
        trail_m=trail,
        brake_release_m=release * step if release is not None else None,
        throttle_pickup_m=pickup * step if pickup is not None else None,
        full_throttle_m=full * step if full is not None else None,
        steering_corrections=count_corrections(steer, step),
    )


def lap_metrics(grid: GridLap, corners: list[Corner]) -> list[CornerMetrics]:
    return [corner_metrics(grid, c) for c in corners]
