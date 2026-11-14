"""Calibration des seuils du coach sur la dispersion naturelle du pilote.

Un conseil n'a de sens que si l'écart à la référence dépasse le « bruit » du pilote d'un
tour à l'autre. Seuil calibré = max(seuil de base, 1,5 x dispersion médiane), plafonné à
3 x le seuil de base. Un pilote régulier garde donc les seuils fins par défaut.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from simengineer.core.config import CoachConfig
from simengineer.store.db import Store

MS_TO_KMH = 3.6
MIN_LAPS = 3
SIGMA_FACTOR = 1.5
MAX_FACTOR = 3.0

# seuil de CoachConfig -> (métrique stockée, facteur d'unité)
_METRICS: dict[str, tuple[str, float]] = {
    "brake_point_m": ("brake_point_m", 1.0),
    "brake_peak": ("brake_peak", 1.0),
    "brake_rise_s": ("brake_rise_s", 1.0),
    "trail_m": ("trail_m", 1.0),
    "min_speed_kmh": ("min_speed", MS_TO_KMH),
    "throttle_pickup_m": ("throttle_pickup_m", 1.0),
    "exit_speed_kmh": ("exit_speed", MS_TO_KMH),
}


def calibration_key(track: str, car: str) -> str:
    return f"thresholds:{track}:{car}"


def measure_dispersion(store: Store, track: str, car: str) -> dict[str, float]:
    """Dispersion médiane (écart-type par virage et par séance) de chaque métrique."""
    sigmas: dict[str, list[float]] = defaultdict(list)
    for s in store.sessions(track=track, car=car):
        groups: dict[tuple[int, str], list[float]] = defaultdict(list)
        for r in store.corner_results(s.id):
            for name, (metric, unit) in _METRICS.items():
                value = r["metrics"].get(metric)
                if value is not None:
                    groups[(int(r["corner"]), name)].append(float(value) * unit)
        for (_corner, name), values in groups.items():
            if len(values) >= MIN_LAPS:
                sigmas[name].append(statistics.pstdev(values))
    return {name: statistics.median(vals) for name, vals in sigmas.items() if vals}


def calibrate(store: Store, track: str, car: str, base: CoachConfig) -> dict[str, float]:
    """Calcule et enregistre les seuils calibrés pour ce circuit et cette voiture."""
    thresholds: dict[str, float] = {}
    for name, sigma in measure_dispersion(store, track, car).items():
        default = float(getattr(base, name))
        thresholds[name] = round(min(max(default, SIGMA_FACTOR * sigma), MAX_FACTOR * default), 3)
    if thresholds:
        store.kv_set(calibration_key(track, car), thresholds)
    return thresholds


def apply_calibration(store: Store, track: str, car: str, base: CoachConfig) -> CoachConfig:
    """Configuration du coach avec les seuils calibrés, s'il y en a pour ce circuit/voiture."""
    stored = store.kv_get(calibration_key(track, car))
    if not stored:
        return base
    known = {k: float(v) for k, v in stored.items() if k in _METRICS}
    return base.model_copy(update=known)
