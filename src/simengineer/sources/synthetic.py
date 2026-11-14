"""Source synthétique déterministe : circuit, pilote, trafic, drapeaux, carburant.

Permet de tout tester sans iRacing (RÈGLE 6). Le modèle est volontairement simple :
profil de vitesse par contraintes (freinage à décélération constante, vitesse mini
tenue jusqu'à la reprise des gaz, accélération limitée) avec des « styles » par virage
pour injecter des défauts connus (freinage trop tôt, vitesse mini trop basse, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from simengineer.core.types import UNLIMITED_LAPS, FloatArray, SessionInfo
from simengineer.core.vars import VARS, CarLeftRight, TrackSurface
from simengineer.sources.base import TICK_RATE, RawSession, RawSessionSource

DS = 0.5  # pas de la grille de distance (m)
A_BRAKE = 13.0  # décélération à pression 1.0 (m/s^2)
A_ACCEL = 6.5  # accélération à basse vitesse (m/s^2)
A_LAT = 14.0  # accélération latérale au point de corde (m/s^2)
COAST_DECEL = 1.6  # décélération en roue libre (frein moteur + aéro), m/s^2
WHEELBASE = 2.46
STEER_RATIO = 12.0
N_CARS = 64


@dataclass(frozen=True, slots=True)
class CornerDef:
    name: str
    apex_m: float
    min_speed: float  # m/s
    direction: int  # +1 gauche, -1 droite
    length_m: float = 120.0


@dataclass(frozen=True, slots=True)
class TrackDef:
    name: str
    length_m: float
    corners: tuple[CornerDef, ...]
    top_speed: float = 72.0


@dataclass(frozen=True, slots=True)
class CornerStyle:
    """Écarts du pilote sur un virage (0 = tour idéal du modèle)."""

    brake_offset_m: float = 0.0  # négatif = freine plus tôt
    brake_peak: float = 0.95
    brake_ramp_m: float = 8.0
    trail_m: float = 35.0
    min_speed_delta: float = 0.0  # m/s
    throttle_offset_m: float = 0.0  # positif = reprend plus tard
    steering_corrections: int = 0
    lift_m: float = 0.0  # lever de pied (roue libre) avant le freinage : économie de carburant


LapStyle = tuple[tuple[int, CornerStyle], ...]  # (index de virage, style), hachable


def lap_style(**by_corner: CornerStyle) -> LapStyle:
    """lap_style(c3=CornerStyle(brake_offset_m=-15)) -> style du virage d'index 3."""
    return tuple(sorted((int(k[1:]), v) for k, v in by_corner.items()))


DEFAULT_TRACK = TrackDef(
    name="Synthetic Ring",
    length_m=4200.0,
    corners=(
        CornerDef("T01", 520.0, 24.0, -1, 110.0),
        CornerDef("T02", 980.0, 38.0, 1, 140.0),
        CornerDef("T03", 1500.0, 20.0, -1, 100.0),
        CornerDef("T04", 2050.0, 45.0, 1, 160.0),
        CornerDef("T05", 2600.0, 28.0, 1, 120.0),
        CornerDef("T06", 3050.0, 33.0, -1, 130.0),
        CornerDef("T07", 3520.0, 22.0, -1, 100.0),
        CornerDef("T08", 3950.0, 36.0, 1, 130.0),
    ),
)


@dataclass(frozen=True, slots=True)
class LapProfile:
    s: FloatArray  # distance (m), pas DS, longueur n
    v: FloatArray
    t: FloatArray  # temps écoulé à chaque point (longueur n+1, t[-1] = temps au tour)
    throttle: FloatArray
    brake: FloatArray
    steering: FloatArray
    lat_accel: FloatArray
    long_accel: FloatArray
    yaw_rate: FloatArray
    curvature: FloatArray

    @property
    def lap_time(self) -> float:
        return float(self.t[-1])


@lru_cache(maxsize=64)
def build_lap_profile(track: TrackDef, style: LapStyle = ()) -> LapProfile:
    """Profil d'un tour en régime établi (tour lancé) pour un style donné."""
    n = round(track.length_m / DS)
    s = np.arange(n, dtype=np.float64) * DS
    styles = dict(style)
    limit = np.full(n, track.top_speed)
    brake_zone = np.zeros(n)  # pression de frein « demandée »
    hold_zone = np.zeros(n, dtype=bool)
    thr_start = np.full(n, -1.0)  # distance depuis le point de reprise, sinon -1
    curvature = np.zeros(n)
    wiggle = np.zeros(n)

    for ci, c in enumerate(track.corners):
        st = styles.get(ci, CornerStyle())
        vmin = c.min_speed + st.min_speed_delta
        a_b = A_BRAKE * st.brake_peak
        s_vmin = c.apex_m - 15.0 + st.brake_offset_m
        s_thr = c.apex_m + 10.0 + st.throttle_offset_m
        # Contrainte de freinage : jusqu'à 400 m avant, avec repliement sur le tour.
        d = (s_vmin - s) % track.length_m  # distance restante avant le point de vitesse mini
        braking = d <= 400.0
        v_brake = np.sqrt(vmin**2 + 2.0 * a_b * d)
        limit = np.where(braking & (v_brake < limit), v_brake, limit)
        hold = ((s - s_vmin) % track.length_m) < max(s_thr - s_vmin, 0.0)
        limit = np.where(hold & (vmin < limit), vmin, limit)
        hold_zone |= hold
        # Forme de pression : montée puis dégressif (trail) vers le point de vitesse mini.
        trail = np.clip(d / max(st.trail_m, 0.5), 0.0, 1.0)
        brake_zone = np.where(braking, np.maximum(brake_zone, st.brake_peak * trail), brake_zone)
        after = (s - s_thr) % track.length_m
        thr_start = np.where(after < 300.0, after, thr_start)
        sigma = c.length_m / 4.0
        dd = (s - c.apex_m + track.length_m / 2) % track.length_m - track.length_m / 2
        bump = np.exp(-0.5 * (dd / sigma) ** 2)
        curvature += c.direction * (A_LAT / c.min_speed**2) * bump
        if st.steering_corrections:
            phase = 2 * np.pi * st.steering_corrections * (dd + c.length_m / 2) / c.length_m
            wiggle += 0.09 * np.sin(phase) * (np.abs(dd) < c.length_m / 2)
        # montée en pression : mémorisée par virage pour être appliquée après la passe avant
    lim = limit.tolist()

    def forward(coast: np.ndarray[Any, Any] | None) -> tuple[FloatArray, np.ndarray[Any, Any]]:
        v = np.empty(n)
        limited = np.zeros(n, dtype=bool)
        cur = float(min(lim[0], track.top_speed))
        co = coast.tolist() if coast is not None else None
        for _ in range(2):  # deux passes : régime périodique
            for i in range(n):
                if co is not None and co[i]:
                    nxt = max(cur * cur - 2.0 * COAST_DECEL * DS, 1.0) ** 0.5
                else:
                    a = A_ACCEL * max(0.0, 1.0 - (cur / track.top_speed) ** 2)
                    nxt = (cur * cur + 2.0 * a * DS) ** 0.5
                li = lim[i]
                if nxt >= li:
                    nxt = li
                    limited[i] = li < track.top_speed - 1e-9
                else:
                    limited[i] = False
                v[i] = nxt
                cur = nxt
        return v, limited

    def corner_of(i: int) -> int:
        return int(np.argmin([(c.apex_m - s[i]) % track.length_m for c in track.corners]))

    v, limited = forward(None)
    coast = np.zeros(n, dtype=bool)
    if any(st.lift_m > 0 for st in styles.values()):
        mask = limited & ~hold_zone & (brake_zone > 0)
        for b0 in np.flatnonzero(mask & ~np.roll(mask, 1)):
            lift = styles.get(corner_of(int(b0)), CornerStyle()).lift_m
            if lift > 0:
                coast[(b0 - np.arange(1, int(lift / DS) + 1)) % n] = True
        v, limited = forward(coast)
        limited &= ~coast

    braking_mask = limited & ~hold_zone & (brake_zone > 0)
    brake = np.where(braking_mask, brake_zone, 0.0)
    # Montée en pression : rampe depuis le début de chaque zone de freinage.
    starts = np.flatnonzero(braking_mask & ~np.roll(braking_mask, 1))
    for b0 in starts:
        ci = corner_of(int(b0))
        ramp_m = max(styles.get(ci, CornerStyle()).brake_ramp_m, DS)
        k = int(ramp_m / DS) + 1
        idx = (b0 + np.arange(k)) % n
        brake[idx] = np.minimum(brake[idx], brake_zone[idx] * (np.arange(k) * DS / ramp_m + 0.1))
    brake = np.clip(brake, 0.0, 1.0)

    throttle = np.where(braking_mask, 0.0, 1.0)
    throttle = np.where(hold_zone & ~braking_mask, 0.12, throttle)
    ramp = np.clip(thr_start / 25.0, 0.0, 1.0)
    throttle = np.where((thr_start >= 0) & ~hold_zone & ~braking_mask, 0.3 + 0.7 * ramp, throttle)
    throttle = np.where(coast & ~braking_mask, 0.0, throttle)

    v_next = np.roll(v, -1)
    dt = DS / np.maximum((v + v_next) / 2.0, 1.0)
    t = np.concatenate(([0.0], np.cumsum(dt)))
    long_accel = (v_next**2 - v**2) / (2.0 * DS)
    lat_accel = v**2 * curvature
    steering = curvature * WHEELBASE * STEER_RATIO + wiggle
    yaw_rate = v * curvature
    return LapProfile(
        s, v, t, throttle, brake, steering, lat_accel, long_accel, yaw_rate, curvature
    )


@dataclass(frozen=True, slots=True)
class OtherCar:
    """Autre voiture : position = s_pilote(min(t, stop_at) * pace) + gap_m."""

    idx: int
    gap_m: float  # position initiale relative au pilote (positif = devant)
    pace: float = 1.0  # 0 = immobile dès le départ
    stop_at_s: float | None = None
    stopped_surface: int = int(TrackSurface.ON_TRACK)
    side: str = "left"  # côté quand elle est à hauteur du pilote
    in_pits: bool = False


@dataclass(frozen=True, slots=True)
class Scenario:
    track: TrackDef = DEFAULT_TRACK
    n_laps: int = 5
    base_style: LapStyle = ()
    lap_styles: tuple[tuple[int, LapStyle], ...] = ()  # (index de tour, style)
    others: tuple[OtherCar, ...] = ()
    flag_periods: tuple[tuple[float, float, int], ...] = ()  # (t0, t1, bits)
    offtracks: tuple[tuple[int, float, float], ...] = ()  # (tour, pct0, pct1)
    incidents: tuple[tuple[int, float, int], ...] = ()  # (tour, pct, points)
    pit_stops: tuple[tuple[int, float], ...] = ()  # (tour d'entrée, litres ajoutés)
    fuel_start_l: float = 60.0
    fuel_per_lap_l: float = 2.5
    fuel_capacity_l: float = 100.0
    race_laps: int | None = None
    race_time_s: float | None = None
    session_type: str = "Practice"
    car_name: str = "Porsche 911 GT3 Cup (992)"
    track_temp_c: float = 32.0
    track_temp_drift_c: float = 0.0  # variation totale sur la session
    wetness: int = 1
    tyre_wear_per_lap: float = 0.012
    noise: float = 0.0
    seed: int = 0
    first_lap: int = 1
    fuel_throttle_model: bool = False  # conso proportionnelle à l'accélérateur (lever de pied)
    incident_limit: int | None = None
    pit_speed_limit_kmh: float = 60.0
    pit_drive_kmh: float | None = (
        None  # vitesse réelle dans la voie des stands (défaut : limite - 1)
    )
    extra: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


def _n(key: str) -> str:
    return VARS[key].name


def generate(sc: Scenario) -> RawSession:
    """Génère toutes les colonnes de la session (noms iRacing via VARS)."""
    track = sc.track
    length = track.length_m
    styles = dict(sc.lap_styles)
    profiles = [build_lap_profile(track, styles.get(i, sc.base_style)) for i in range(sc.n_laps)]
    # Concaténation des tours : temps et distance cumulés.
    t_parts, s_parts, t0 = [], [], 0.0
    for i, p in enumerate(profiles):
        t_parts.append(t0 + p.t[:-1])
        s_parts.append(i * length + p.s)
        t0 += p.lap_time
    t_all = np.concatenate([*t_parts, [t0]])
    s_all = np.concatenate([*s_parts, [sc.n_laps * length]])
    n_frames = int(t0 * TICK_RATE)
    time = np.arange(n_frames) / TICK_RATE
    s_tot = np.interp(time, t_all, s_all)
    lap_i = np.minimum((s_tot // length).astype(np.int64), sc.n_laps - 1)
    s_lap = s_tot - lap_i * length
    pct = s_lap / length

    def chan(attr: str) -> FloatArray:
        vals = np.concatenate([getattr(p, attr) for p in profiles])
        vals = np.concatenate([vals, vals[-1:]])
        return np.asarray(np.interp(time, t_all, vals), dtype=np.float64)

    rng = np.random.default_rng(sc.seed)
    speed = chan("v")
    if sc.noise:
        speed = speed + rng.normal(0.0, sc.noise, n_frames)
    cols: dict[str, np.ndarray[Any, Any]] = {
        _n("session_time"): time,
        _n("lap"): lap_i + sc.first_lap,
        _n("lap_dist_pct"): pct,
        _n("lap_dist"): s_lap,
        _n("speed"): speed,
        _n("throttle"): np.clip(chan("throttle"), 0, 1),
        _n("brake"): np.clip(chan("brake"), 0, 1),
        _n("steering"): chan("steering"),
        _n("lat_accel"): chan("lat_accel"),
        _n("long_accel"): chan("long_accel"),
        _n("yaw_rate"): chan("yaw_rate"),
        _n("gear"): np.clip((speed // 13).astype(np.int64) + 1, 1, 6),
        _n("rpm"): 4500.0 + (speed % 13) * 300.0,
        _n("is_on_track"): np.ones(n_frames, dtype=np.bool_),
        _n("player_car_idx"): np.zeros(n_frames, dtype=np.int64),
        _n("session_state"): np.full(n_frames, 4, dtype=np.int64),
    }
    # Position (Lat/Lon) : fonction de la distance au tour (même tracé à chaque tour).
    # Tracé fermé : virage global réparti pour totaliser un tour complet, puis la petite
    # dérive de fermeture est corrigée linéairement (géométrie de démonstration seulement).
    geo = profiles[0]
    turn = np.cumsum(geo.curvature * DS)
    heading = turn + (2.0 * np.pi - turn[-1]) * geo.s / track.length_m
    gx, gy = np.cumsum(np.cos(heading) * DS), np.cumsum(np.sin(heading) * DS)
    frac = geo.s / track.length_m
    gx, gy = gx - gx[-1] * frac, gy - gy[-1] * frac
    x = np.interp(s_lap, geo.s, gx)
    y = np.interp(s_lap, geo.s, gy)
    cols[_n("lat")] = 45.0 + y / 111_320.0
    cols[_n("lon")] = 5.0 + x / (111_320.0 * np.cos(np.radians(45.0)))

    # Stands, carburant, pneus.
    on_pit = np.zeros(n_frames, dtype=np.bool_)
    if sc.fuel_throttle_model:
        # Débit ∝ (ralenti + accélérateur) le long de la distance ; un tour du style de base
        # consomme exactement fuel_per_lap_l.
        def burn(p: LapProfile) -> FloatArray:
            return np.asarray(0.08 + 0.92 * p.throttle, dtype=np.float64)

        norm = float(burn(build_lap_profile(track, sc.base_style)).sum())
        cum = np.concatenate([[0.0], np.cumsum(np.concatenate([burn(p) for p in profiles]))])
        s_grid = np.concatenate([s_all[:-1], [s_all[-1]]])
        used = np.interp(s_tot, s_grid, cum[: len(s_grid)]) * sc.fuel_per_lap_l / norm
        fuel = sc.fuel_start_l - used
    else:
        fuel = sc.fuel_start_l - sc.fuel_per_lap_l * s_tot / length
    wear_laps = s_tot / length
    for pit_lap, added in sc.pit_stops:
        entering = (lap_i == pit_lap) & (pct > 0.93)
        leaving = (lap_i == pit_lap + 1) & (pct < 0.05)
        on_pit |= entering | leaving
        after = lap_i > pit_lap
        fuel = np.where(after, fuel + added, fuel)
        wear_laps = np.where(after, s_tot / length - (pit_lap + 1), wear_laps)
    cols[_n("on_pit_road")] = on_pit
    cols[_n("fuel_level")] = np.maximum(fuel, 0.0)
    pit_drive = (sc.pit_drive_kmh or sc.pit_speed_limit_kmh - 1.0) / 3.6
    cols[_n("speed")] = np.where(on_pit, np.minimum(speed, pit_drive), speed)

    surface = np.full(n_frames, int(TrackSurface.ON_TRACK), dtype=np.int64)
    surface[on_pit] = int(TrackSurface.APPROACHING_PITS)
    for lap, p0, p1 in sc.offtracks:
        surface[(lap_i == lap) & (pct >= p0) & (pct <= p1)] = int(TrackSurface.OFF_TRACK)
    cols[_n("player_track_surface")] = surface
    inc = np.zeros(n_frames, dtype=np.int64)
    for lap, p0, points in sc.incidents:
        inc[(lap_i > lap) | ((lap_i == lap) & (pct >= p0))] += points
    cols[_n("incident_count")] = inc

    flags = np.zeros(n_frames, dtype=np.uint32)
    for f0, f1, bits in sc.flag_periods:
        flags[(time >= f0) & (time < f1)] |= np.uint32(bits)
    cols[_n("session_flags")] = flags

    # Temps au tour officiels.
    lap_times = np.array([p.lap_time for p in profiles])
    starts = np.concatenate(([0.0], np.cumsum(lap_times)))
    cols[_n("lap_current_time")] = time - starts[lap_i]
    last = np.where(lap_i > 0, lap_times[np.maximum(lap_i - 1, 0)], -1.0)
    cols[_n("lap_last_time")] = last

    # Durée de course.
    if sc.race_time_s is not None:
        cols[_n("session_time_remain")] = np.maximum(sc.race_time_s - time, 0.0)
        cols[_n("session_laps_remain")] = np.full(n_frames, UNLIMITED_LAPS, dtype=np.int64)
    elif sc.race_laps is not None:
        cols[_n("session_time_remain")] = np.full(n_frames, 604800.0)
        cols[_n("session_laps_remain")] = np.maximum(sc.race_laps - lap_i, 0)
    else:
        cols[_n("session_time_remain")] = np.full(n_frames, 604800.0)
        cols[_n("session_laps_remain")] = np.full(n_frames, UNLIMITED_LAPS, dtype=np.int64)

    # Autres voitures.
    car_pct = np.full((n_frames, N_CARS), -1.0, dtype=np.float32)
    car_surf = np.full((n_frames, N_CARS), int(TrackSurface.NOT_IN_WORLD), dtype=np.int8)
    car_pit = np.zeros((n_frames, N_CARS), dtype=np.bool_)
    car_pct[:, 0] = pct
    car_surf[:, 0] = surface
    car_pit[:, 0] = on_pit
    n_left = np.zeros(n_frames, dtype=np.int64)
    n_right = np.zeros(n_frames, dtype=np.int64)
    totals: dict[int, FloatArray] = {0: s_tot}  # distance totale parcourue (classement)
    for car in sc.others:
        t_car = time if car.stop_at_s is None else np.minimum(time, car.stop_at_s)
        if car.pace == 0.0:
            s_car = np.full(n_frames, car.gap_m)
        else:
            # Décalage en temps (et non en distance) : la voiture suit le même profil de
            # vitesse le long de la piste, sans effet d'accordéon artificiel.
            lap0 = profiles[0].lap_time
            t_gap = float(np.interp(car.gap_m % length, s_all, t_all))
            if car.gap_m < 0:
                t_gap -= lap0
            tt = t_car * car.pace + t_gap
            # Au-delà de la fin de la chronologie (voiture plus rapide) : on reboucle.
            total, dist = t_all[-1], s_all[-1]
            wraps = np.floor(tt / total)
            s_car = np.where(
                tt >= 0,
                np.interp(tt - wraps * total, t_all, s_all) + wraps * dist,
                np.interp(tt + lap0, t_all, s_all) - length,
            )
        car_pct[:, car.idx] = (s_car % length) / length
        if not car.in_pits:
            totals[car.idx] = np.asarray(s_car, dtype=np.float64)
        surf = np.full(n_frames, int(TrackSurface.ON_TRACK), dtype=np.int64)
        if car.stop_at_s is not None:
            surf[time >= car.stop_at_s] = car.stopped_surface
        if car.in_pits:
            surf[:] = int(TrackSurface.IN_PIT_STALL)
            car_pit[:, car.idx] = True
        car_surf[:, car.idx] = surf
        rel = (s_car - s_tot + length / 2) % length - length / 2
        beside = (np.abs(rel) < 4.5) & (not car.in_pits)
        if car.side == "left":
            n_left += beside
        else:
            n_right += beside
    clr = np.full(n_frames, int(CarLeftRight.CLEAR), dtype=np.int64)
    clr[(n_left == 1) & (n_right == 0)] = int(CarLeftRight.CAR_LEFT)
    clr[(n_right == 1) & (n_left == 0)] = int(CarLeftRight.CAR_RIGHT)
    clr[(n_left >= 1) & (n_right >= 1)] = int(CarLeftRight.CAR_LEFT_RIGHT)
    clr[(n_left >= 2) & (n_right == 0)] = int(CarLeftRight.TWO_CARS_LEFT)
    clr[(n_right >= 2) & (n_left == 0)] = int(CarLeftRight.TWO_CARS_RIGHT)
    clr[on_pit] = int(CarLeftRight.OFF)
    cols[_n("car_left_right")] = clr
    cols[_n("car_idx_lap_dist_pct")] = car_pct
    cols[_n("car_idx_track_surface")] = car_surf
    cols[_n("car_idx_on_pit_road")] = car_pit

    # Météo et pneus (les pneus ne changent qu'aux stands, comme supposé pour iRacing).
    cols[_n("track_temp")] = sc.track_temp_c + sc.track_temp_drift_c * time / max(t0, 1.0)
    cols[_n("air_temp")] = np.full(n_frames, 22.0)
    cols[_n("track_wetness")] = np.full(n_frames, sc.wetness, dtype=np.int64)
    cols[_n("precipitation")] = np.zeros(n_frames)
    stint_start_wear = 1.0 - sc.tyre_wear_per_lap * np.floor(s_tot / length - wear_laps)
    temps = sc.extra.get("tyre_temps", {})
    for corner in ("lf", "rf", "lr", "rr"):
        for pos, base in (("cl", 82.0), ("cm", 80.0), ("cr", 78.0)):
            key = f"tyre_{corner}_temp_{pos}"
            cols[_n(key)] = np.full(n_frames, float(temps.get(key, base)))
        for pos in ("l", "m", "r"):
            cols[_n(f"tyre_{corner}_wear_{pos}")] = stint_start_wear
        cols[_n(f"tyre_{corner}_pressure")] = np.full(n_frames, 175.0)

    # Classement (distance totale) et écart au leader en temps (≈ distance / vitesse moyenne).
    idxs = sorted(totals)
    dist = np.stack([totals[i] for i in idxs], axis=1)
    order = np.argsort(-dist, axis=1, kind="stable")
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.arange(1, len(idxs) + 1)[None, :].repeat(n_frames, 0), 1)
    car_pos = np.zeros((n_frames, N_CARS), dtype=np.int16)
    car_f2 = np.full((n_frames, N_CARS), -1.0, dtype=np.float32)
    v_avg = length / profiles[0].lap_time
    lead = dist.max(axis=1)
    for k, i in enumerate(idxs):
        car_pos[:, i] = ranks[:, k]
        car_f2[:, i] = (lead - dist[:, k]) / v_avg
    cols[_n("car_idx_position")] = car_pos
    cols[_n("car_idx_f2_time")] = car_f2

    info = SessionInfo(
        track_name=track.name,
        track_length_m=length,
        car_name=sc.car_name,
        session_type=sc.session_type,
        player_car_idx=0,
        fuel_capacity_l=sc.fuel_capacity_l,
        session_laps=sc.race_laps,
        session_time_s=sc.race_time_s,
        incident_limit=sc.incident_limit,
        pit_speed_limit_kmh=sc.pit_speed_limit_kmh,
        drivers={0: "Pilote", **{c.idx: f"Voiture {c.idx}" for c in sc.others}},
    )
    meta = {s.name: (s.unit, "synthétique") for s in VARS.values()}
    return RawSession(info=info, columns=cols, tick_rate=TICK_RATE, var_meta=meta)


def gap_for_distance_at(track: TrackDef, t: float, distance_m: float) -> float:
    """`gap_m` initial pour qu'une voiture (pace 1) soit `distance_m` devant à l'instant t."""
    p = build_lap_profile(track)
    s_t = float(np.interp(t, p.t[:-1], p.s))
    t_gap = float(np.interp(s_t + distance_m, p.s, p.t[:-1])) - t
    return float(np.interp(t_gap, p.t[:-1], p.s))


class SyntheticSource(RawSessionSource):
    def __init__(
        self, scenario: Scenario | None = None, realtime: bool = False, speed: float = 1.0
    ) -> None:
        super().__init__(generate(scenario or Scenario()), "synthetic", realtime, speed)
