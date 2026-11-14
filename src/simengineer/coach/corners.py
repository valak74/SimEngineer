"""Détection des virages sur un tour rééchantillonné (grille 1 m)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

import numpy as np

from simengineer.core.config import CoachConfig
from simengineer.core.types import FloatArray
from simengineer.telemetry.resample import GridLap


@dataclass(frozen=True, slots=True)
class Corner:
    number: int  # 1..N dans l'ordre du tour
    start_m: float  # début de l'appui latéral
    apex_m: float  # point de vitesse minimale du tour de détection
    end_m: float
    zone_start_m: float  # zones : partition complète du tour (pour répartir le temps)
    zone_end_m: float
    direction: int  # signe de l'accélération latérale (convention du sim, non interprétée)

    @property
    def name(self) -> str:
        return f"T{self.number:02d}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Corner:
        return Corner(**d)


def smooth(x: FloatArray, window: int) -> FloatArray:
    if window <= 1:
        return x
    kernel = np.ones(window) / window
    padded = np.concatenate([x[-window:], x, x[:window]])  # le tour est périodique
    return np.asarray(np.convolve(padded, kernel, mode="same")[window:-window], dtype=np.float64)


def _corner_signal(grid: GridLap, cfg: CoachConfig) -> tuple[FloatArray, float]:
    """Signal de courbure et seuil : accélération latérale si présente, sinon volant."""
    if grid.has("lat_accel"):
        return smooth(grid.ch("lat_accel"), 15), cfg.corner_lat_accel_ms2
    steer = smooth(grid.ch("steering"), 15)
    return steer, 0.25 * float(np.max(np.abs(steer))) if len(steer) else 1.0


def detect_corners(grid: GridLap, cfg: CoachConfig) -> list[Corner]:
    signal, threshold = _corner_signal(grid, cfg)
    step = grid.step
    active = np.abs(signal) > threshold
    edges = np.diff(np.concatenate(([0], active.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    segments: list[list[int]] = []
    for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
        sign = int(np.sign(signal[s:e].mean()))
        prev = segments[-1] if segments else None
        if prev and prev[2] == sign and (s - prev[1]) * step < cfg.corner_merge_gap_m:
            prev[1] = e  # même virage, appui brièvement relâché
        else:
            segments.append([s, e, sign])
    segments = [g for g in segments if (g[1] - g[0]) * step >= cfg.corner_min_length_m]
    speed = grid.ch("speed")
    n = grid.n
    bounds = [0.0]
    for a, b in pairwise(segments):
        bounds.append((a[1] + b[0]) / 2.0 * step)
    bounds.append(n * step)
    corners = []
    for i, (s, e, sign) in enumerate(segments):
        apex = s + int(np.argmin(speed[s:e]))
        corners.append(
            Corner(i + 1, s * step, apex * step, e * step, bounds[i], bounds[i + 1], sign)
        )
    return corners
