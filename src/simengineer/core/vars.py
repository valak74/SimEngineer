"""Constante unique `VARS` : toutes les variables de télémétrie iRacing utilisées.

RÈGLE 7 : aucun autre module ne doit écrire un nom de variable iRacing en dur.
Les noms ci-dessous sont des HYPOTHÈSES tant que le pilote n'a pas lancé
`simengineer dump-vars` avec iRacing réel (voir docs/ASSUMPTIONS.md). Ils sont
validés au démarrage contre la liste réelle exposée par la source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag


@dataclass(frozen=True, slots=True)
class VarSpec:
    key: str  # nom logique interne
    name: str  # nom iRacing supposé
    required: bool
    unit: str
    note: str = ""


def _v(key: str, name: str, required: bool, unit: str, note: str = "") -> VarSpec:
    return VarSpec(key, name, required, unit, note)


_SPECS: tuple[VarSpec, ...] = (
    # --- Temps / session
    _v("session_time", "SessionTime", True, "s"),
    _v("session_time_remain", "SessionTimeRemain", False, "s"),
    _v("session_laps_remain", "SessionLapsRemainEx", False, "tours"),
    _v("session_flags", "SessionFlags", False, "bitfield"),
    _v("session_state", "SessionState", False, "enum"),
    _v("session_num", "SessionNum", False, "idx"),
    # --- Voiture du pilote
    _v("lap", "Lap", True, "tours"),
    _v("lap_dist_pct", "LapDistPct", True, "0..1"),
    _v("lap_dist", "LapDist", False, "m"),
    _v("lap_current_time", "LapCurrentLapTime", False, "s"),
    _v("lap_last_time", "LapLastLapTime", False, "s"),
    _v("speed", "Speed", True, "m/s"),
    _v("throttle", "Throttle", True, "0..1"),
    _v("brake", "Brake", True, "0..1"),
    _v("steering", "SteeringWheelAngle", True, "rad"),
    _v("gear", "Gear", False, ""),
    _v("rpm", "RPM", False, "tr/min"),
    _v("fuel_level", "FuelLevel", False, "L"),
    _v("on_pit_road", "OnPitRoad", False, "bool"),
    _v("is_on_track", "IsOnTrack", False, "bool"),
    _v("player_track_surface", "PlayerTrackSurface", False, "enum irsdk_TrkLoc"),
    _v("player_car_idx", "PlayerCarIdx", False, "idx"),
    _v("incident_count", "PlayerCarMyIncidentCount", False, "x"),
    _v("lat_accel", "LatAccel", False, "m/s^2"),
    _v("long_accel", "LongAccel", False, "m/s^2"),
    _v("yaw_rate", "YawRate", False, "rad/s"),
    _v("lat", "Lat", False, "deg", "précision de trajectoire à mesurer"),
    _v("lon", "Lon", False, "deg", "précision de trajectoire à mesurer"),
    # --- Autres voitures (tableaux par CarIdx)
    _v("car_left_right", "CarLeftRight", False, "enum irsdk_CarLeftRight"),
    _v("car_idx_lap_dist_pct", "CarIdxLapDistPct", False, "0..1 par voiture"),
    _v("car_idx_track_surface", "CarIdxTrackSurface", False, "enum par voiture"),
    _v("car_idx_on_pit_road", "CarIdxOnPitRoad", False, "bool par voiture"),
    _v("car_idx_lap", "CarIdxLap", False, "tours par voiture"),
    _v("car_idx_position", "CarIdxPosition", False, "position par voiture"),
    _v("car_idx_f2_time", "CarIdxF2Time", False, "s, écart au leader par voiture"),
    # --- Météo / piste (lues à 1 Hz)
    _v("track_temp", "TrackTempCrew", False, "C"),
    _v("air_temp", "AirTemp", False, "C"),
    _v("track_wetness", "TrackWetness", False, "enum"),
    _v("precipitation", "Precipitation", False, "0..1"),
    # --- Pneus (iRacing ne rafraîchit ces valeurs qu'aux stands : hypothèse)
    *(
        _v(f"tyre_{c.lower()}_temp_{pos.lower()}", f"{c}temp{pos}", False, "C")
        for c in ("LF", "RF", "LR", "RR")
        for pos in ("CL", "CM", "CR")
    ),
    *(
        _v(f"tyre_{c.lower()}_wear_{pos.lower()}", f"{c}wear{pos}", False, "0..1")
        for c in ("LF", "RF", "LR", "RR")
        for pos in ("L", "M", "R")
    ),
    *(
        _v(f"tyre_{c.lower()}_pressure", f"{c}pressure", False, "kPa")
        for c in ("LF", "RF", "LR", "RR")
    ),
)

VARS: dict[str, VarSpec] = {spec.key: spec for spec in _SPECS}

# Clés lues à chaque image (60 Hz) ; le reste est lu à ~1 Hz dans `Frame.slow`.
FAST_KEYS: tuple[str, ...] = tuple(
    k
    for k in VARS
    if not k.startswith("tyre_")
    and k not in ("track_temp", "air_temp", "track_wetness", "precipitation")
)
SLOW_KEYS: tuple[str, ...] = tuple(k for k in VARS if k not in FAST_KEYS)


class TrackSurface(IntEnum):
    """irsdk_TrkLoc (hypothèse, à vérifier)."""

    NOT_IN_WORLD = -1
    OFF_TRACK = 0
    IN_PIT_STALL = 1
    APPROACHING_PITS = 2
    ON_TRACK = 3


class CarLeftRight(IntEnum):
    """irsdk_CarLeftRight (hypothèse, à vérifier)."""

    OFF = 0
    CLEAR = 1
    CAR_LEFT = 2
    CAR_RIGHT = 3
    CAR_LEFT_RIGHT = 4
    TWO_CARS_LEFT = 5
    TWO_CARS_RIGHT = 6


class Flags(IntFlag):
    """irsdk_Flags (hypothèse, à vérifier)."""

    CHECKERED = 0x0001
    WHITE = 0x0002
    GREEN = 0x0004
    YELLOW = 0x0008
    RED = 0x0010
    BLUE = 0x0020
    DEBRIS = 0x0040
    YELLOW_WAVING = 0x0100
    CAUTION = 0x4000
    CAUTION_WAVING = 0x8000
    BLACK = 0x010000
    DISQUALIFY = 0x020000
    REPAIR = 0x100000


YELLOW_MASK = int(Flags.YELLOW | Flags.YELLOW_WAVING | Flags.CAUTION | Flags.CAUTION_WAVING)
FULL_COURSE_YELLOW_MASK = int(Flags.CAUTION | Flags.CAUTION_WAVING)


class VarsValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VarsReport:
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_required


def check_vars(available: Iterable[str]) -> VarsReport:
    names = set(available)
    req = tuple(s.name for s in VARS.values() if s.required and s.name not in names)
    opt = tuple(s.name for s in VARS.values() if not s.required and s.name not in names)
    return VarsReport(req, opt)


def validate_vars(available: Iterable[str]) -> VarsReport:
    """Valide `VARS` au démarrage. Lève si une variable obligatoire est absente."""
    report = check_vars(available)
    if not report.ok:
        raise VarsValidationError(
            "Variables de télémétrie obligatoires absentes : "
            + ", ".join(report.missing_required)
            + ". Lancez `simengineer dump-vars` et corrigez core/vars.py."
        )
    return report
