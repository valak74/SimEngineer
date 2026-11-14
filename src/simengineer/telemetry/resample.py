"""Rééchantillonnage d'un tour sur une grille de distance régulière (1 m par défaut)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from simengineer.core.types import FloatArray
from simengineer.telemetry.laps import Lap

GRID_STEP_M = 1.0


@dataclass(slots=True)
class GridLap:
    """Tour sur grille de distance : `channels[nom][i]` = valeur à `i * step` mètres."""

    track_length_m: float
    lap_time: float
    channels: dict[str, FloatArray]
    step: float = GRID_STEP_M
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.channels["time"])

    @property
    def dist(self) -> FloatArray:
        return np.arange(self.n, dtype=np.float64) * self.step

    def ch(self, name: str) -> FloatArray:
        return self.channels[name]

    def has(self, name: str) -> bool:
        return name in self.channels and bool(np.isfinite(self.channels[name]).any())


def resample_lap(lap: Lap, step: float = GRID_STEP_M) -> GridLap:
    length = lap.track_length_m
    grid = np.arange(0.0, length, step)
    dist = np.maximum.accumulate(lap.channels["dist"])
    t = lap.channels["time"]
    # Bornes exactes : distance 0 au temps 0, longueur de piste au temps du tour.
    d_ext = np.concatenate(([0.0], dist, [length]))
    t_ext = np.concatenate(([0.0], t, [lap.lap_time]))
    order = np.argsort(d_ext, kind="stable")
    channels: dict[str, FloatArray] = {"time": np.interp(grid, d_ext[order], t_ext[order])}
    for name, values in lap.channels.items():
        if name in ("time", "dist"):
            continue
        channels[name] = np.interp(grid, dist, values)
    meta = {"number": lap.number, "valid": lap.valid, **lap.flags()}
    return GridLap(length, lap.lap_time, channels, step, meta)


def save_lap_npz(path: Path, grid: GridLap, lap: Lap | None = None) -> None:
    """Un fichier par tour : grille 1 m (`g_*`), données brutes 60 Hz (`r_*`), métadonnées."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {f"g_{k}": v.astype(np.float32) for k, v in grid.channels.items()}
    arrays["g_time"] = grid.channels["time"]  # le temps reste en double précision
    if lap is not None:
        arrays.update({f"r_{k}": v.astype(np.float32) for k, v in lap.channels.items()})
    meta = {
        "track_length_m": grid.track_length_m,
        "lap_time": grid.lap_time,
        "step": grid.step,
        **grid.meta,
    }
    arrays["meta"] = np.array(json.dumps(meta))
    np.savez(path, **arrays)


def load_grid_npz(path: Path) -> GridLap:
    with np.load(path) as z:
        meta = json.loads(str(z["meta"]))
        channels = {k[2:]: z[k].astype(np.float64) for k in z.files if k.startswith("g_")}
    return GridLap(
        track_length_m=float(meta.pop("track_length_m")),
        lap_time=float(meta.pop("lap_time")),
        step=float(meta.pop("step")),
        channels=channels,
        meta=meta,
    )
