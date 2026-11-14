"""Segmentation du flux 60 Hz en tours, avec drapeaux de validité."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from simengineer.core.types import FloatArray, Frame
from simengineer.core.vars import YELLOW_MASK, TrackSurface

# Canaux enregistrés par image (ordre des colonnes du tampon).
CHANNELS: tuple[str, ...] = (
    "time",
    "dist",
    "speed",
    "throttle",
    "brake",
    "steering",
    "lat_accel",
    "long_accel",
    "yaw_rate",
    "gear",
    "rpm",
    "lat",
    "lon",
)
_NCH = len(CHANNELS)


@dataclass(slots=True)
class Lap:
    number: int
    lap_time: float
    start_session_time: float
    track_length_m: float
    channels: dict[str, FloatArray]  # échantillons temporels ; "time" relatif au début du tour
    complete: bool = True
    pit: bool = False
    offtrack: bool = False
    incident: bool = False
    yellow: bool = False
    fuel_start: float = math.nan
    fuel_end: float = math.nan
    official_time: float = math.nan
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """Tour exploitable pour le coaching et les références."""
        return self.complete and not (self.pit or self.offtrack or self.incident)

    @property
    def clean_for_fuel(self) -> bool:
        """Tour représentatif pour la consommation (ni stand, ni neutralisation)."""
        return self.complete and not (self.pit or self.yellow)

    @property
    def fuel_used(self) -> float:
        return self.fuel_start - self.fuel_end

    def flags(self) -> dict[str, bool]:
        return {
            "complete": self.complete,
            "pit": self.pit,
            "offtrack": self.offtrack,
            "incident": self.incident,
            "yellow": self.yellow,
        }


class LapSegmenter:
    """Accumule les images dans un tampon préalloué et émet un `Lap` à chaque passage de ligne."""

    def __init__(self, track_length_m: float, capacity: int = 60 * 240) -> None:
        if track_length_m <= 0:
            raise ValueError("Longueur de piste inconnue : impossible de segmenter les tours")
        self.track_length_m = track_length_m
        self._buf = np.empty((capacity, _NCH), dtype=np.float64)
        self._n = 0
        self._prev: Frame | None = None
        self._start_time = math.nan  # instant (interpolé) du passage de ligne
        self._reset_flags(complete=False)
        self._lap_number = 0
        self._fuel_start = math.nan
        self._inc_start = 0

    def _reset_flags(self, complete: bool) -> None:
        self._complete = complete
        self._pit = False
        self._offtrack = False
        self._incident = False
        self._yellow = False

    def _begin(self, frame: Frame, start_time: float, complete: bool) -> None:
        self._n = 0
        self._start_time = start_time
        self._reset_flags(complete)
        self._lap_number = frame.lap
        self._fuel_start = frame.fuel_level
        self._inc_start = frame.incident_count

    def _emit(self, end_time: float, fuel_end: float, official: float) -> Lap:
        data = self._buf[: self._n].copy()
        data[:, 0] -= self._start_time
        return Lap(
            number=self._lap_number,
            lap_time=end_time - self._start_time,
            start_session_time=self._start_time,
            track_length_m=self.track_length_m,
            channels={name: data[:, i] for i, name in enumerate(CHANNELS)},
            complete=self._complete,
            pit=self._pit,
            offtrack=self._offtrack,
            incident=self._incident,
            yellow=self._yellow,
            fuel_start=self._fuel_start,
            fuel_end=fuel_end,
            official_time=official,
        )

    def on_frame(self, f: Frame) -> Lap | None:
        """Traite une image ; renvoie le tour terminé le cas échéant."""
        length = self.track_length_m
        prev = self._prev
        self._prev = f
        finished: Lap | None = None
        if prev is None:
            # Début d'enregistrement : le tour n'est complet que si l'on démarre sur la ligne.
            on_line = f.lap_dist_pct * length < 5.0 and f.speed > 5.0
            start = f.session_time - (f.lap_dist_pct * length / f.speed if on_line else 0.0)
            self._begin(f, start, complete=on_line)
        else:
            dp = f.lap_dist_pct - prev.lap_dist_pct
            if prev.lap_dist_pct > 0.85 and f.lap_dist_pct < 0.15:
                before = 1.0 - prev.lap_dist_pct
                span = before + f.lap_dist_pct
                frac = before / span if span > 0 else 0.0
                cross = prev.session_time + (f.session_time - prev.session_time) * frac
                finished = self._emit(cross, prev.fuel_level, f.lap_last_time)
                self._begin(f, cross, complete=True)
            elif abs(dp) * length > 60.0 or f.session_time < prev.session_time:
                # Téléportation (retour aux stands, reset) : tour inexploitable.
                self._complete = False
        if self._n >= len(self._buf):
            self._buf = np.concatenate([self._buf, np.empty_like(self._buf)])
        row = self._buf[self._n]
        row[0] = f.session_time
        row[1] = f.lap_dist_pct * length
        row[2] = f.speed
        row[3] = f.throttle
        row[4] = f.brake
        row[5] = f.steering
        row[6] = f.lat_accel
        row[7] = f.long_accel
        row[8] = f.yaw_rate
        row[9] = f.gear
        row[10] = f.rpm
        row[11] = f.lat
        row[12] = f.lon
        self._n += 1
        if f.on_pit_road or f.player_track_surface in (
            TrackSurface.IN_PIT_STALL,
            TrackSurface.APPROACHING_PITS,
        ):
            self._pit = True
        if f.player_track_surface == TrackSurface.OFF_TRACK:
            self._offtrack = True
        if f.incident_count > self._inc_start:
            self._incident = True
        if f.session_flags & YELLOW_MASK:
            self._yellow = True
        return finished

    def flush(self) -> Lap | None:
        """Tour partiel en cours (marqué incomplet), à la fin de la session."""
        if self._prev is None or self._n == 0:
            return None
        self._complete = False
        return self._emit(self._prev.session_time, self._prev.fuel_level, math.nan)
