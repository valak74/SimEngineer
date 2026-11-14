"""Règles pures du spotter : aucune E/S, aucun état, aucun réseau (règle 2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from simengineer.core.types import FloatArray, IntArray
from simengineer.core.vars import (
    FULL_COURSE_YELLOW_MASK,
    YELLOW_MASK,
    CarLeftRight,
    Flags,
    TrackSurface,
)
from simengineer.voice.arbiter import Priority

_SIDE_KEYS: dict[int, str] = {
    int(CarLeftRight.CAR_LEFT): "car_left",
    int(CarLeftRight.CAR_RIGHT): "car_right",
    int(CarLeftRight.CAR_LEFT_RIGHT): "cars_both",
    int(CarLeftRight.TWO_CARS_LEFT): "two_left",
    int(CarLeftRight.TWO_CARS_RIGHT): "two_right",
}
_STILL_KEYS: dict[int, str] = {
    int(CarLeftRight.CAR_LEFT): "still_left",
    int(CarLeftRight.TWO_CARS_LEFT): "still_left",
    int(CarLeftRight.CAR_RIGHT): "still_right",
    int(CarLeftRight.TWO_CARS_RIGHT): "still_right",
}


def side_key(state: int) -> str | None:
    """Gabarit pour un état CarLeftRight « voiture à côté », sinon None."""
    return _SIDE_KEYS.get(state)


def still_key(state: int) -> str | None:
    return _STILL_KEYS.get(state)


def is_side_occupied(state: int) -> bool:
    return state in _SIDE_KEYS


def forward_gap_m(player_pct: float, other_pct: FloatArray, track_length_m: float) -> FloatArray:
    """Distance le long de la piste jusqu'à chaque voiture DEVANT le pilote (0..longueur)."""
    return np.asarray(((other_pct - player_pct) % 1.0) * track_length_m, dtype=np.float64)


def estimate_speeds(
    old_pct: FloatArray, new_pct: FloatArray, dt: float, track_length_m: float
) -> FloatArray:
    """Vitesse (m/s) de chaque voiture par variation de position sur la piste."""
    if dt <= 0:
        return np.zeros_like(new_pct)
    d = (new_pct - old_pct + 0.5) % 1.0 - 0.5  # gère le passage de la ligne
    return np.asarray(d * track_length_m / dt, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class Hazard:
    car_idx: int
    distance_m: float
    offtrack: bool


def slow_cars_ahead(
    player_idx: int,
    player_pct: float,
    pcts: FloatArray,
    speeds: FloatArray,
    surfaces: IntArray,
    on_pit: NDArray[np.bool_] | None,
    track_length_m: float,
    max_distance_m: float,
    speed_threshold_ms: float,
) -> list[Hazard]:
    """Voitures quasi arrêtées devant, sur ou au bord de la piste, triées par distance.

    iRacing ne donne pas la position latérale : on ne peut annoncer que la distance.
    """
    in_world = (surfaces == TrackSurface.ON_TRACK) | (surfaces == TrackSurface.OFF_TRACK)
    mask = in_world & (pcts >= 0.0) & (np.abs(speeds) < speed_threshold_ms)
    if on_pit is not None:
        mask &= ~on_pit
    if 0 <= player_idx < len(mask):
        mask[player_idx] = False
    idxs = np.flatnonzero(mask)
    if idxs.size == 0:
        return []
    gaps = forward_gap_m(player_pct, pcts[idxs], track_length_m)
    out = [
        Hazard(int(i), float(g), bool(surfaces[i] == TrackSurface.OFF_TRACK))
        for i, g in zip(idxs, gaps, strict=True)
        if 5.0 < g <= max_distance_m
    ]
    out.sort(key=lambda h: h.distance_m)
    return out


@dataclass(frozen=True, slots=True)
class Closing:
    car_idx: int
    gap_s: float  # écart en temps derrière le pilote
    rate: float  # secondes gagnées par seconde


class PlayerTrail:
    """Instant du dernier passage du pilote à chaque point du tour (pas de 5 m).

    Écart en temps d'une voiture derrière = maintenant - instant où le pilote était à sa
    position. Contrairement à l'écart en mètres, il ne fait pas l'accordéon aux freinages.
    """

    def __init__(self, track_length_m: float, step_m: float = 5.0) -> None:
        self.n = max(int(track_length_m / step_m), 1)
        self.times = np.full(self.n, np.nan)
        self._last: int | None = None

    def update(self, pct: float, t: float) -> None:
        b = int(pct * self.n) % self.n
        last = self._last
        if last is None or (b - last) % self.n > self.n // 2:  # départ ou recul : un seul point
            self.times[b] = t
        else:
            i = (last + 1 + np.arange((b - last) % self.n)) % self.n
            self.times[i] = t
        self._last = b

    def gaps(self, pcts: FloatArray, t: float) -> FloatArray:
        b = (np.clip(pcts, 0.0, 0.999999) * self.n).astype(np.int64)
        g = t - self.times[b]
        return np.asarray(np.where(pcts >= 0.0, g, np.nan), dtype=np.float64)


def gaps_behind(player_pct: float, pcts: FloatArray, track_length_m: float) -> FloatArray:
    """Distance (m) de chaque voiture DERRIÈRE le pilote, le long de la piste."""
    return np.asarray(((player_pct - pcts) % 1.0) * track_length_m, dtype=np.float64)


def faster_cars_behind(
    player_idx: int,
    gap_s_now: FloatArray,
    gap_s_before: FloatArray,
    gap_m_now: FloatArray,
    dt: float,
    surfaces: IntArray,
    on_pit: NDArray[np.bool_] | None,
    max_gap_s: float,
    max_distance_m: float,
    min_rate: float,
) -> list[Closing]:
    """Voitures derrière qui reprennent du temps, à moins de `max_gap_s` secondes.

    Basé uniquement sur `CarIdxLapDistPct` : pas de classe ni de côté.
    """
    if dt <= 0:
        return []
    mask = surfaces == TrackSurface.ON_TRACK
    if on_pit is not None:
        mask &= ~on_pit
    if 0 <= player_idx < len(mask):
        mask[player_idx] = False
    with np.errstate(invalid="ignore"):
        rate = (gap_s_before - gap_s_now) / dt
        keep = (
            mask
            & np.isfinite(rate)
            & (gap_m_now > 12.0)  # à hauteur : c'est CarLeftRight qui annonce
            & (gap_m_now <= max_distance_m)
            & (gap_s_now > 0.0)
            & (gap_s_now <= max_gap_s)
            & (rate >= min_rate)
            & (rate < 1.0)  # saut (tour bouclé, téléportation) : ignoré
        )
    return sorted(
        (Closing(int(i), float(gap_s_now[i]), float(rate[i])) for i in np.flatnonzero(keep)),
        key=lambda c: c.gap_s,
    )


def announce_distance(distance_m: float) -> int:
    """Distance arrondie à 50 m (au moins 50) : phrases pré-synthétisées."""
    return max(50, round(distance_m / 50.0) * 50)


def flag_calls(prev: int, cur: int) -> list[tuple[str, Priority]]:
    """Annonces à faire lors d'un changement de drapeaux."""
    new = cur & ~prev
    calls: list[tuple[str, Priority]] = []
    if new & Flags.RED:
        calls.append(("flag_red", Priority.P0))
    if new & FULL_COURSE_YELLOW_MASK and not prev & FULL_COURSE_YELLOW_MASK:
        calls.append(("flag_fcy", Priority.P0))
    elif new & YELLOW_MASK and not prev & YELLOW_MASK:
        calls.append(("flag_yellow", Priority.P0))
    if new & Flags.DEBRIS:
        calls.append(("flag_debris", Priority.P0))
    if new & Flags.BLACK:
        calls.append(("flag_black", Priority.P0))
    yellow_cleared = bool(prev & YELLOW_MASK) and not cur & YELLOW_MASK
    if yellow_cleared or (new & Flags.GREEN and prev != 0):
        calls.append(("flag_green", Priority.P1))
    if new & Flags.BLUE:
        calls.append(("flag_blue", Priority.P1))
    if new & Flags.WHITE:
        calls.append(("flag_white", Priority.P1))
    if new & Flags.CHECKERED:
        calls.append(("flag_checkered", Priority.P1))
    return calls
