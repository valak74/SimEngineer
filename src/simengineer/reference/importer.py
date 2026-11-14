"""Import de tours de référence depuis des fichiers que le pilote possède (.ibt, CSV).

Aucun téléchargement, aucun scraping (règle 8). Provenance et droits sont obligatoires.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from simengineer.core.types import FloatArray
from simengineer.reference.store import ReferenceRightsError, RefRow, add_reference
from simengineer.sources.base import RawSessionSource
from simengineer.sources.ibt_format import read_ibt
from simengineer.store.db import Store
from simengineer.telemetry.laps import Lap, LapSegmenter
from simengineer.telemetry.resample import GRID_STEP_M, GridLap, resample_lap


class ReferenceImportError(ValueError):
    pass


def _check_rights(source: str, rights: str) -> None:
    if not source.strip() or not rights.strip():
        raise ReferenceRightsError(
            "Provenance (--source) et droits d'usage (--rights) obligatoires : seules des"
            " données que tu possèdes peuvent servir de référence."
        )


def import_ibt(
    store: Store,
    path: Path,
    source: str,
    rights: str,
    label: str = "",
    lap_number: int | None = None,
) -> RefRow:
    """Importe le meilleur tour valide d'un fichier .ibt (ou le tour `lap_number`)."""
    _check_rights(source, rights)
    raw = read_ibt(path)
    info = raw.info
    seg = LapSegmenter(info.track_length_m)
    laps: list[Lap] = [
        lap
        for f in RawSessionSource(raw, "ibt_replay").frames()
        if (lap := seg.on_frame(f)) is not None
    ]
    if lap_number is not None:
        chosen = [lap for lap in laps if lap.number == lap_number and lap.complete]
    else:
        chosen = [lap for lap in laps if lap.valid]
    if not chosen:
        raise ReferenceImportError(f"{path.name} : aucun tour complet et valide à importer")
    best = min(chosen, key=lambda lap: lap.lap_time)
    grid = resample_lap(best)
    return add_reference(
        store, grid, info.track_name, info.car_name, source, rights, label, path.name
    )


# Noms de colonnes acceptés dans les CSV (comparés en minuscules, sans espaces ni unités).
_ALIASES: dict[str, tuple[str, ...]] = {
    "dist": ("distance", "dist", "lapdist", "lapdistance", "distancem"),
    "pct": ("lapdistpct", "pct", "lappct", "distpct"),
    "time": ("time", "laptime", "elapsed", "t", "lapcurrentlaptime", "times"),
    "speed": ("speed", "speedkmh", "speedms", "v", "velocity", "speedkph"),
    "throttle": ("throttle", "gas", "tps", "throttlepct"),
    "brake": ("brake", "brakepct", "brakepressure"),
    "steering": ("steering", "steer", "steeringwheelangle", "steeringangle", "swa"),
    "lat_accel": ("lataccel", "latg", "lateralacceleration"),
    "long_accel": ("longaccel", "longg", "longitudinalacceleration"),
    "gear": ("gear",),
    "rpm": ("rpm",),
}


def _norm(header: str) -> str:
    h = header.lower().split("(")[0].split("[")[0]
    return "".join(ch for ch in h if ch.isalnum())


def _read_csv(path: Path) -> tuple[dict[str, FloatArray], dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if len(rows) < 3:
        raise ReferenceImportError(f"{path.name} : CSV vide")
    headers = rows[0]
    mapping: dict[str, int] = {}
    raw_names: dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _norm(h)
        for chan, names in _ALIASES.items():
            if key in names and chan not in mapping:
                mapping[chan] = i
                raw_names[chan] = h
    decimal_comma = dialect.delimiter == ";"
    cols: dict[str, list[float]] = {c: [] for c in mapping}
    for row in rows[1:]:
        if len(row) < len(headers):
            continue
        try:
            vals = {
                c: float(row[i].replace(",", ".") if decimal_comma else row[i])
                for c, i in mapping.items()
            }
        except ValueError:
            continue  # ligne d'unités ou ligne incomplète
        for c, v in vals.items():
            cols[c].append(v)
    return {c: np.asarray(v, dtype=np.float64) for c, v in cols.items()}, raw_names


def csv_to_grid(path: Path, track_length_m: float | None = None) -> GridLap:
    """Convertit un CSV d'un tour (distance ou % du tour, vitesse, pédales…) en grille 1 m."""
    cols, raw_names = _read_csv(path)
    if "speed" not in cols or not ("dist" in cols or "pct" in cols):
        raise ReferenceImportError(
            f"{path.name} : colonnes requises introuvables (distance ou lapdistpct, et speed)."
            f" Colonnes reconnues : {sorted(raw_names.values())}"
        )
    speed = cols["speed"]
    if (
        "km" in raw_names["speed"].lower()
        or "kph" in raw_names["speed"].lower()
        or speed.max() > 120
    ):
        speed = speed / 3.6
    if "dist" in cols:
        dist = cols["dist"] - cols["dist"][0]
        if dist.max() < 50.0 and "km" in raw_names["dist"].lower():
            dist = dist * 1000.0
        step = float(np.median(np.diff(dist)))
        length = track_length_m or float(dist[-1] + step)
    else:
        if not track_length_m:
            raise ReferenceImportError(
                f"{path.name} : avec une colonne en % du tour, --track-length est obligatoire"
            )
        pct = cols["pct"] / (100.0 if cols["pct"].max() > 1.5 else 1.0)
        length = track_length_m
        dist = pct * length
    if len(dist) < 50 or length < 500.0:
        raise ReferenceImportError(f"{path.name} : tour trop court ou incomplet")
    dist = np.maximum.accumulate(dist)
    if "time" in cols:
        t = cols["time"] - cols["time"][0]
        if t.max() > 3600.0:  # millisecondes
            t = t / 1000.0
        tail = (length - dist[-1]) / max(speed[-1], 1.0)
        lap_time = float(t[-1] + max(tail, 0.0))
    else:
        seg = np.diff(np.concatenate((dist, [length])))
        t = np.concatenate(([0.0], np.cumsum(seg / np.maximum(speed, 1.0))))
        lap_time, t = float(t[-1]), t[:-1]
    grid = np.arange(0.0, length, GRID_STEP_M)
    channels: dict[str, FloatArray] = {
        "time": np.interp(grid, np.concatenate((dist, [length])), np.concatenate((t, [lap_time]))),
        "speed": np.interp(grid, dist, speed),
    }
    for name in ("throttle", "brake"):
        vals = cols.get(name, np.zeros_like(dist))
        if vals.size and vals.max() > 1.5:
            vals = vals / 100.0  # pédales en pourcentage
        channels[name] = np.clip(np.interp(grid, dist, vals), 0.0, 1.0)
    steer = cols.get("steering", np.zeros_like(dist))
    if steer.size and np.abs(steer).max() > 7.0:
        steer = np.radians(steer)  # volant en degrés
    channels["steering"] = np.interp(grid, dist, steer)
    for name in ("lat_accel", "long_accel", "gear", "rpm"):
        if name in cols:
            channels[name] = np.interp(grid, dist, cols[name])
    return GridLap(length, lap_time, channels, GRID_STEP_M, {"number": 0, "valid": True})


def import_csv(
    store: Store,
    path: Path,
    track: str,
    car: str,
    source: str,
    rights: str,
    label: str = "",
    track_length_m: float | None = None,
) -> RefRow:
    _check_rights(source, rights)
    if not track.strip() or not car.strip():
        raise ReferenceImportError("--track et --car sont obligatoires pour un CSV")
    grid = csv_to_grid(path, track_length_m)
    return add_reference(store, grid, track.strip(), car.strip(), source, rights, label, path.name)
