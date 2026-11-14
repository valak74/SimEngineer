"""Instantané « direct » pour l'interface : uniquement des données déjà calculées.

Construit dans la boucle principale au plus `refresh_hz` fois par seconde (coût < 0,1 ms),
puis publié par simple affectation : le serveur HTTP le lit sans verrou.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from simengineer.core.types import Frame
from simengineer.core.vars import FULL_COURSE_YELLOW_MASK, YELLOW_MASK, Flags, TrackSurface
from simengineer.llm.tools import ToolRegistry
from simengineer.telemetry.position import EARTH_M_PER_DEG
from simengineer.telemetry.resample import GridLap

if TYPE_CHECKING:
    from simengineer.app import SimEngineerApp

MAP_STEP_M = 8.0
MAP_SIZE = 1000.0


def waiting_snapshot(version: int = 0) -> dict[str, Any]:
    return {"status": "waiting", "version": version}


def flag_state(flags: int) -> str:
    if flags & Flags.CHECKERED:
        return "checkered"
    if flags & Flags.RED:
        return "red"
    if flags & FULL_COURSE_YELLOW_MASK:
        return "fcy"
    if flags & YELLOW_MASK:
        return "yellow"
    if flags & Flags.WHITE:
        return "white"
    if flags & Flags.BLUE:
        return "blue"
    return "green"


def _clean(value: Any) -> Any:
    """NaN/inf -> None pour un JSON valide."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def build_snapshot(
    app: SimEngineerApp, tools: ToolRegistry, f: Frame | None, version: int, track_version: int
) -> dict[str, Any]:
    info = app.info
    snap: dict[str, Any] = {
        "status": "live",
        "version": version,
        "track_version": track_version,
        "session": {
            "id": app.session_id,
            "track": info.track_name,
            "car": info.car_name,
            "type": info.session_type,
            "source": app.source.name,
        },
        "fuel": tools.execute("get_fuel_status", {}).data,
        "pit": tools.execute("get_pit_window", {}).data,
        "laps": tools.execute("get_lap_summary", {}).data,
        "gap": tools.execute("get_gap", {}).data,
        "tyres": tools.execute("get_tyres", {}).data,
        "weather": tools.execute("get_weather", {}).data,
        "ref_label": app.coach.reference_label,
        "spoken": [{"p": int(u.priority), "text": u.text} for u in app.arbiter.spoken[-12:][::-1]],
    }
    race = app.engineer.state.race if app.engineer is not None else None
    snap["race"] = (
        {
            "position": race.position,
            "gained": race.gained,
            "incidents": race.incidents,
            "incident_limit": race.incident_limit,
            "finished": race.finished,
        }
        if race is not None
        else None
    )
    training = app.coach.training
    snap["training"] = (
        {
            "theme": training.theme,
            "corners": [f"T{c:02d}" for c in training.active_corners],
            "messages": {f"T{c:02d}": training.message(c) for c in training.active_corners},
        }
        if training is not None
        else (
            {"theme": None, "corners": [], "messages": {}} if app.cfg.coach.training_mode else None
        )
    )
    analysis = app.coach.last_analysis
    snap["advice"] = (
        {
            "lap": analysis.lap_number,
            "delta_s": analysis.delta_s,
            "items": [
                {"corner": f"T{a.corner:02d}", "gain_s": a.gain_s, "text": a.long}
                for a in analysis.top(3)
            ],
        }
        if analysis
        else None
    )
    if f is not None:
        snap["car"] = {
            "lap": f.lap,
            "pct": f.lap_dist_pct,
            "speed_kmh": f.speed * 3.6,
            "gear": f.gear,
            "throttle": f.throttle,
            "brake": f.brake,
            "in_pits": f.on_pit_road,
        }
        snap["flag"] = flag_state(f.session_flags)
        pcts, surf = f.car_idx_lap_dist_pct, f.car_idx_track_surface
        if pcts is not None and surf is not None:
            mask = (pcts >= 0) & (surf == TrackSurface.ON_TRACK)
            if 0 <= f.player_car_idx < len(mask):
                mask[f.player_car_idx] = False
            snap["others"] = [round(float(p), 4) for p in pcts[mask]]
    return _clean(snap)  # type: ignore[no-any-return]


def track_shape(grid: GridLap, corners: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Tracé du circuit (Lat/Lon du tour de référence) normalisé dans un carré de 1000."""
    if not (grid.has("lat") and grid.has("lon")):
        return None
    step = max(1, int(MAP_STEP_M / grid.step))
    lat, lon = grid.ch("lat")[::step], grid.ch("lon")[::step]
    x = (lon - lon.mean()) * EARTH_M_PER_DEG * np.cos(np.radians(lat.mean()))
    y = -(lat - lat.mean()) * EARTH_M_PER_DEG
    span = max(float(np.ptp(x)), float(np.ptp(y)), 1.0)
    scale = (MAP_SIZE - 80.0) / span
    x = (x - x.min()) * scale + (MAP_SIZE - np.ptp(x) * scale) / 2
    y = (y - y.min()) * scale + (MAP_SIZE - np.ptp(y) * scale) / 2
    n = len(x)
    return {
        "points": [[round(float(a), 1), round(float(b), 1)] for a, b in zip(x, y, strict=True)],
        "length_m": grid.track_length_m,
        "corners": [
            {
                "name": c.get("label") or f"T{int(c['number']):02d}",
                "i": min(int(c["apex_m"] / (step * grid.step)), n - 1),
            }
            for c in corners
        ],
    }
