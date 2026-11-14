"""Types de base partagés : image de télémétrie et informations de session."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

NAN = math.nan
UNLIMITED_LAPS = 32767  # valeur iRacing pour "illimité" (hypothèse, à vérifier)


@dataclass(slots=True)
class Frame:
    """Une image de télémétrie (60 Hz). Champs facultatifs à NaN/None si absents."""

    tick: int
    session_time: float
    lap: int
    lap_dist_pct: float
    speed: float
    throttle: float
    brake: float
    steering: float
    gear: int = 0
    rpm: float = 0.0
    fuel_level: float = NAN
    on_pit_road: bool = False
    is_on_track: bool = True
    player_track_surface: int = 3
    session_flags: int = 0
    session_state: int = 4
    session_time_remain: float = NAN
    session_laps_remain: int = -1  # -1 = inconnu / illimité
    incident_count: int = 0
    lap_last_time: float = NAN
    lap_current_time: float = NAN
    lat_accel: float = NAN
    long_accel: float = NAN
    yaw_rate: float = NAN
    lat: float = NAN
    lon: float = NAN
    car_left_right: int = 0
    player_car_idx: int = 0
    car_idx_lap_dist_pct: FloatArray | None = None
    car_idx_track_surface: IntArray | None = None
    car_idx_on_pit_road: NDArray[np.bool_] | None = None
    car_idx_position: IntArray | None = None
    # Variables lentes (pneus, météo), présentes ~1 fois par seconde, indexées par clé VARS.
    slow: dict[str, float] | None = None


@dataclass(slots=True)
class SessionInfo:
    track_name: str = "inconnu"
    track_length_m: float = 0.0
    car_name: str = "inconnu"
    session_type: str = "Practice"
    player_car_idx: int = 0
    fuel_capacity_l: float = 0.0
    session_laps: int | None = None  # None = course au temps / illimité
    session_time_s: float | None = None
    # Noms des pilotes : restent en local, jamais envoyés au LLM.
    drivers: dict[int, str] = field(default_factory=dict)

    @property
    def is_race(self) -> bool:
        return self.session_type.lower().startswith("race")


@dataclass(frozen=True, slots=True)
class VarDescription:
    name: str
    type: str
    count: int
    unit: str
    desc: str
