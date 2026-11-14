"""Interface commune des sources de télémétrie et construction des `Frame`."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from simengineer.core.types import UNLIMITED_LAPS, Frame, SessionInfo, VarDescription
from simengineer.core.vars import SLOW_KEYS, VARS, validate_vars

TICK_RATE = 60


class TelemetrySource(Protocol):
    name: str

    def open(self) -> None: ...
    def session_info(self) -> SessionInfo: ...
    def available_vars(self) -> list[VarDescription]: ...
    def frames(self) -> Iterator[Frame]: ...
    def close(self) -> None: ...


class FrameBuilder:
    """Construit des `Frame` depuis un accès brut `get(nom_iRacing)`.

    Seul point du code où les noms `VARS` sont lus dans la télémétrie.
    """

    def __init__(self, available: Iterable[str]) -> None:
        names = set(available)
        self.report = validate_vars(names)
        self._n = {k: s.name for k, s in VARS.items() if s.name in names}
        self._slow = [(k, VARS[k].name) for k in SLOW_KEYS if VARS[k].name in names]

    def has(self, key: str) -> bool:
        return key in self._n

    def build(self, tick: int, get: Callable[[str], Any], with_slow: bool = False) -> Frame:
        n = self._n
        f = Frame(
            tick=tick,
            session_time=float(get(n["session_time"])),
            lap=int(get(n["lap"])),
            lap_dist_pct=float(get(n["lap_dist_pct"])),
            speed=float(get(n["speed"])),
            throttle=float(get(n["throttle"])),
            brake=float(get(n["brake"])),
            steering=float(get(n["steering"])),
        )
        if "gear" in n:
            f.gear = int(get(n["gear"]))
        if "rpm" in n:
            f.rpm = float(get(n["rpm"]))
        if "fuel_level" in n:
            f.fuel_level = float(get(n["fuel_level"]))
        if "on_pit_road" in n:
            f.on_pit_road = bool(get(n["on_pit_road"]))
        if "is_on_track" in n:
            f.is_on_track = bool(get(n["is_on_track"]))
        if "player_track_surface" in n:
            f.player_track_surface = int(get(n["player_track_surface"]))
        if "session_flags" in n:
            f.session_flags = int(get(n["session_flags"])) & 0xFFFFFFFF
        if "session_state" in n:
            f.session_state = int(get(n["session_state"]))
        if "session_time_remain" in n:
            f.session_time_remain = float(get(n["session_time_remain"]))
        if "session_laps_remain" in n:
            laps = int(get(n["session_laps_remain"]))
            f.session_laps_remain = -1 if laps >= UNLIMITED_LAPS or laps < 0 else laps
        if "incident_count" in n:
            f.incident_count = int(get(n["incident_count"]))
        if "lap_last_time" in n:
            f.lap_last_time = float(get(n["lap_last_time"]))
        if "lap_current_time" in n:
            f.lap_current_time = float(get(n["lap_current_time"]))
        if "lat_accel" in n:
            f.lat_accel = float(get(n["lat_accel"]))
        if "long_accel" in n:
            f.long_accel = float(get(n["long_accel"]))
        if "yaw_rate" in n:
            f.yaw_rate = float(get(n["yaw_rate"]))
        if "lat" in n:
            f.lat = float(get(n["lat"]))
        if "lon" in n:
            f.lon = float(get(n["lon"]))
        if "car_left_right" in n:
            f.car_left_right = int(get(n["car_left_right"]))
        if "player_car_idx" in n:
            f.player_car_idx = int(get(n["player_car_idx"]))
        if "car_idx_lap_dist_pct" in n:
            f.car_idx_lap_dist_pct = np.asarray(get(n["car_idx_lap_dist_pct"]), dtype=np.float64)
        if "car_idx_track_surface" in n:
            f.car_idx_track_surface = np.asarray(get(n["car_idx_track_surface"]), dtype=np.int64)
        if "car_idx_on_pit_road" in n:
            f.car_idx_on_pit_road = np.asarray(get(n["car_idx_on_pit_road"]), dtype=np.bool_)
        if "car_idx_position" in n:
            f.car_idx_position = np.asarray(get(n["car_idx_position"]), dtype=np.int64)
        if with_slow and self._slow:
            f.slow = {k: float(get(name)) for k, name in self._slow}
        return f


@dataclass
class RawSession:
    """Session complète en mémoire : colonnes indexées par nom iRacing."""

    info: SessionInfo
    columns: dict[str, np.ndarray[Any, Any]]
    tick_rate: int = TICK_RATE
    var_meta: dict[str, tuple[str, str]] = field(default_factory=dict)  # nom -> (unité, desc)

    @property
    def n_frames(self) -> int:
        return len(next(iter(self.columns.values()))) if self.columns else 0

    def describe(self) -> list[VarDescription]:
        out = []
        for name, col in sorted(self.columns.items()):
            kind = {"f": "float", "i": "int", "u": "bitfield", "b": "bool"}.get(col.dtype.kind, "?")
            count = 1 if col.ndim == 1 else int(col.shape[1])
            unit, desc = self.var_meta.get(name, ("", ""))
            out.append(VarDescription(name, kind, count, unit, desc))
        return out


class RawSessionSource:
    """Rejoue une `RawSession` image par image (temps réel ou accéléré)."""

    def __init__(self, raw: RawSession, name: str, realtime: bool = False) -> None:
        self.name = name
        self._raw = raw
        self._realtime = realtime

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def session_info(self) -> SessionInfo:
        return self._raw.info

    def available_vars(self) -> list[VarDescription]:
        return self._raw.describe()

    def frames(self) -> Iterator[Frame]:
        raw = self._raw
        builder = FrameBuilder(raw.columns.keys())
        # Les colonnes scalaires sont converties en listes : indexation ~10x plus rapide.
        cols: dict[str, Any] = {
            name: (col.tolist() if col.ndim == 1 else col) for name, col in raw.columns.items()
        }
        period = 1.0 / raw.tick_rate
        start = time.perf_counter()
        idx = 0

        def get(name: str) -> Any:
            return cols[name][idx]

        for idx in range(raw.n_frames):
            if self._realtime:
                delay = start + idx * period - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            yield builder.build(idx, get, with_slow=(idx % raw.tick_rate == 0))
