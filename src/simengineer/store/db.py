"""Stockage local : SQLite pour l'index, fichiers NPZ pour les tours (règle 5)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simengineer.core.types import SessionInfo
from simengineer.telemetry.laps import Lap
from simengineer.telemetry.resample import GridLap, load_grid_npz, save_lap_npz

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, source TEXT NOT NULL,
    track TEXT NOT NULL, car TEXT NOT NULL, session_type TEXT NOT NULL,
    track_length_m REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS laps (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
    number INTEGER NOT NULL, lap_time REAL NOT NULL, valid INTEGER NOT NULL,
    flags TEXT NOT NULL, fuel_used REAL, npz_path TEXT
);
CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY, track TEXT NOT NULL, car TEXT NOT NULL, lap_time REAL NOT NULL,
    source TEXT NOT NULL, rights TEXT NOT NULL, label TEXT NOT NULL, origin_file TEXT NOT NULL,
    imported_at TEXT NOT NULL, npz_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corner_results (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, lap_id INTEGER NOT NULL,
    corner INTEGER NOT NULL, loss_s REAL NOT NULL, issue TEXT NOT NULL, metrics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_marks (
    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    after_lap INTEGER NOT NULL, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
CREATE INDEX IF NOT EXISTS idx_corner_session ON corner_results(session_id);
"""


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: int
    started_at: str
    source: str
    track: str
    car: str
    session_type: str
    track_length_m: float


@dataclass(frozen=True, slots=True)
class LapRow:
    id: int
    session_id: int
    number: int
    lap_time: float
    valid: bool
    flags: dict[str, bool]
    fuel_used: float | None
    npz_path: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "simengineer.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- sessions
    def create_session(self, info: SessionInfo, source: str) -> int:
        cur = self.db.execute(
            "INSERT INTO sessions (started_at, source, track, car, session_type, track_length_m)"
            " VALUES (?,?,?,?,?,?)",
            (_now(), source, info.track_name, info.car_name, info.session_type,
             info.track_length_m),
        )  # fmt: skip
        self.db.commit()
        return int(cur.lastrowid or 0)

    def session(self, session_id: int) -> SessionRow:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session {session_id} introuvable")
        return SessionRow(**dict(row))

    def sessions(self, track: str | None = None, car: str | None = None) -> list[SessionRow]:
        sql = "SELECT * FROM sessions WHERE 1=1"
        args: list[str] = []
        if track:
            sql += " AND track=?"
            args.append(track)
        if car:
            sql += " AND car=?"
            args.append(car)
        return [SessionRow(**dict(r)) for r in self.db.execute(sql + " ORDER BY id", args)]

    def last_session_id(self) -> int | None:
        row = self.db.execute("SELECT MAX(id) AS m FROM sessions").fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    # --- tours
    def lap_path(self, session_id: int, number: int) -> Path:
        return self.data_dir / "laps" / f"s{session_id:05d}" / f"lap{number:03d}.npz"

    def add_lap(self, session_id: int, lap: Lap, grid: GridLap | None = None) -> int:
        """Enregistre un tour (et son fichier NPZ si la grille est fournie)."""
        npz: str | None = None
        if grid is not None:
            path = self.lap_path(session_id, lap.number)
            save_lap_npz(path, grid, lap)
            npz = str(path.relative_to(self.data_dir))
        return self.add_lap_row(session_id, lap, npz)

    def add_lap_row(self, session_id: int, lap: Lap, npz: str | None) -> int:
        fuel = lap.fuel_used
        cur = self.db.execute(
            "INSERT INTO laps (session_id, number, lap_time, valid, flags, fuel_used, npz_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (session_id, lap.number, lap.lap_time, int(lap.valid), json.dumps(lap.flags()),
             fuel if fuel == fuel else None, npz),
        )  # fmt: skip
        self.db.commit()
        return int(cur.lastrowid or 0)

    def laps(self, session_id: int, valid_only: bool = False) -> list[LapRow]:
        sql = "SELECT * FROM laps WHERE session_id=?" + (" AND valid=1" if valid_only else "")
        out = []
        for r in self.db.execute(sql + " ORDER BY number", (session_id,)):
            d = dict(r)
            d["valid"] = bool(d["valid"])
            d["flags"] = json.loads(d["flags"])
            out.append(LapRow(**d))
        return out

    def load_grid(self, npz_path: str) -> GridLap:
        return load_grid_npz(self.data_dir / npz_path)

    # --- résultats par virage
    def add_corner_results(
        self, session_id: int, lap_id: int, rows: list[tuple[int, float, str, dict[str, Any]]]
    ) -> None:
        """rows : (numéro de virage, temps perdu, défaut principal ou "", métriques)."""
        self.db.executemany(
            "INSERT INTO corner_results (session_id, lap_id, corner, loss_s, issue, metrics)"
            " VALUES (?,?,?,?,?,?)",
            [(session_id, lap_id, c, loss, issue, json.dumps(m)) for c, loss, issue, m in rows],
        )
        self.db.commit()

    def corner_results(self, session_id: int) -> list[dict[str, Any]]:
        out = []
        for r in self.db.execute(
            "SELECT lap_id, corner, loss_s, issue, metrics FROM corner_results"
            " WHERE session_id=? ORDER BY lap_id, corner",
            (session_id,),
        ):
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    # --- clé/valeur JSON (cartes de virages, seuils calibrés…)
    def kv_set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, json.dumps(value), json.dumps(value)),
        )
        self.db.commit()

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
