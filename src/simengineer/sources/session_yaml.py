"""Infos de session iRacing : extraction minimale depuis le YAML ou les dictionnaires pyirsdk.

Les noms de clés YAML sont des hypothèses (docs/ASSUMPTIONS.md, section B).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from simengineer.core.types import SessionInfo

# Clés YAML supposées, regroupées ici (équivalent de VARS pour les infos de session).
Y_TRACK_NAME = "TrackDisplayName"
Y_TRACK_LENGTH = "TrackLength"
Y_DRIVER_CAR_IDX = "DriverCarIdx"
Y_FUEL_MAX = "DriverCarFuelMaxLtr"
Y_DRIVERS = "Drivers"
Y_CAR_IDX = "CarIdx"
Y_USER_NAME = "UserName"
Y_CAR_NAME = "CarScreenName"
Y_SESSIONS = "Sessions"
Y_SESSION_NUM = "SessionNum"
Y_SESSION_TYPE = "SessionType"
Y_SESSION_LAPS = "SessionLaps"
Y_SESSION_TIME = "SessionTime"


def _num(text: Any) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else None


def session_info_from_dicts(
    weekend: Mapping[str, Any],
    driver_info: Mapping[str, Any],
    sessions: Mapping[str, Any],
    session_num: int = 0,
) -> SessionInfo:
    info = SessionInfo()
    info.track_name = str(weekend.get(Y_TRACK_NAME, info.track_name))
    km = _num(weekend.get(Y_TRACK_LENGTH, ""))
    info.track_length_m = (km or 0.0) * 1000.0
    info.player_car_idx = int(_num(driver_info.get(Y_DRIVER_CAR_IDX, 0)) or 0)
    info.fuel_capacity_l = float(_num(driver_info.get(Y_FUEL_MAX, 0)) or 0.0)
    for drv in driver_info.get(Y_DRIVERS, []) or []:
        idx = int(_num(drv.get(Y_CAR_IDX, -1)) or 0)
        info.drivers[idx] = str(drv.get(Y_USER_NAME, ""))
        if idx == info.player_car_idx:
            info.car_name = str(drv.get(Y_CAR_NAME, info.car_name))
    sess_list = list(sessions.get(Y_SESSIONS, []) or [])
    chosen = next(
        (s for s in sess_list if int(_num(s.get(Y_SESSION_NUM, -1)) or 0) == session_num),
        sess_list[-1] if sess_list else None,
    )
    if chosen:
        info.session_type = str(chosen.get(Y_SESSION_TYPE, info.session_type))
        laps = _num(chosen.get(Y_SESSION_LAPS, ""))
        info.session_laps = int(laps) if laps is not None else None
        secs = _num(chosen.get(Y_SESSION_TIME, ""))
        info.session_time_s = secs if secs is not None else None
    return info


def _scalar_block(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"^\s*-?\s*([A-Za-z0-9_]+):[ \t]*(\S.*)?$", text, re.MULTILINE):
        out.setdefault(m.group(1), (m.group(2) or "").strip().strip('"'))
    return out


def _list_items(text: str, first_key: str) -> list[dict[str, str]]:
    parts = re.split(rf"^\s*-\s+(?={first_key}:)", text, flags=re.MULTILINE)
    return [_scalar_block(p) for p in parts[1:]]


def _section(text: str, name: str) -> str:
    m = re.search(rf"^{name}:\s*$(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def parse_session_yaml(text: str, session_num: int = 0) -> SessionInfo:
    """Extraction par expressions régulières (pas de dépendance YAML, décision D07)."""
    weekend = _scalar_block(_section(text, "WeekendInfo"))
    drv_text = _section(text, "DriverInfo")
    head, _, tail = drv_text.partition(f"{Y_DRIVERS}:")
    driver_info: dict[str, Any] = dict(_scalar_block(head))
    driver_info[Y_DRIVERS] = _list_items(tail, Y_CAR_IDX)
    sessions = {Y_SESSIONS: _list_items(_section(text, "SessionInfo"), Y_SESSION_NUM)}
    return session_info_from_dicts(weekend, driver_info, sessions, session_num)


def session_info_to_yaml(info: SessionInfo) -> str:
    """YAML minimal au format supposé d'iRacing (pour l'écrivain .ibt de test)."""
    laps = "unlimited" if info.session_laps is None else str(info.session_laps)
    secs = "unlimited" if info.session_time_s is None else f"{info.session_time_s:.4f} sec"
    drivers = "".join(
        f" - {Y_CAR_IDX}: {idx}\n   {Y_USER_NAME}: {name}\n   {Y_CAR_NAME}: {info.car_name}\n"
        for idx, name in sorted(info.drivers.items())
    )
    return (
        "---\n"
        "WeekendInfo:\n"
        f" {Y_TRACK_NAME}: {info.track_name}\n"
        f" {Y_TRACK_LENGTH}: {info.track_length_m / 1000.0:.4f} km\n"
        "SessionInfo:\n"
        f" {Y_SESSIONS}:\n"
        f" - {Y_SESSION_NUM}: 0\n"
        f"   {Y_SESSION_TYPE}: {info.session_type}\n"
        f"   {Y_SESSION_LAPS}: {laps}\n"
        f"   {Y_SESSION_TIME}: {secs}\n"
        "DriverInfo:\n"
        f" {Y_DRIVER_CAR_IDX}: {info.player_car_idx}\n"
        f" {Y_FUEL_MAX}: {info.fuel_capacity_l:.3f}\n"
        f" {Y_DRIVERS}:\n"
        f"{drivers}"
        "...\n"
    )
