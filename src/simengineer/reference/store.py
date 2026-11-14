"""Tours de référence en base : uniquement des fichiers que le pilote possède (règle 8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from simengineer.store.db import Store
from simengineer.telemetry.resample import GridLap, save_lap_npz


@dataclass(frozen=True, slots=True)
class RefRow:
    id: int
    track: str
    car: str
    lap_time: float
    source: str  # provenance (ex. « mon tour », « pack Coach Dave acheté le … »)
    rights: str  # droit d'usage déclaré par le pilote
    label: str
    origin_file: str
    imported_at: str
    npz_path: str


class ReferenceRightsError(ValueError):
    pass


def add_reference(
    store: Store,
    grid: GridLap,
    track: str,
    car: str,
    source: str,
    rights: str,
    label: str = "",
    origin_file: str = "",
) -> RefRow:
    """Enregistre une référence. Provenance et droits sont obligatoires."""
    if not source.strip() or not rights.strip():
        raise ReferenceRightsError(
            "Provenance (--source) et droits d'usage (--rights) obligatoires : seules des"
            " données que tu possèdes peuvent servir de référence."
        )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    cur = store.db.execute(
        "INSERT INTO refs (track, car, lap_time, source, rights, label, origin_file,"
        " imported_at, npz_path) VALUES (?,?,?,?,?,?,?,?,?)",
        (track, car, grid.lap_time, source.strip(), rights.strip(), label or source.strip(),
         origin_file, now, ""),
    )  # fmt: skip
    ref_id = int(cur.lastrowid or 0)
    rel = f"refs/ref{ref_id:04d}.npz"
    grid.meta.update({"source": source.strip(), "rights": rights.strip(), "track": track})
    save_lap_npz(store.data_dir / rel, grid)
    store.db.execute("UPDATE refs SET npz_path=? WHERE id=?", (rel, ref_id))
    store.db.commit()
    return get_reference(store, ref_id)


def get_reference(store: Store, ref_id: int) -> RefRow:
    row = store.db.execute("SELECT * FROM refs WHERE id=?", (ref_id,)).fetchone()
    if row is None:
        raise KeyError(f"Référence {ref_id} introuvable")
    return RefRow(**dict(row))


def list_references(store: Store, track: str | None = None, car: str | None = None) -> list[RefRow]:
    sql = "SELECT * FROM refs WHERE 1=1"
    args: list[str] = []
    if track:
        sql += " AND track=?"
        args.append(track)
    if car:
        sql += " AND car=?"
        args.append(car)
    return [RefRow(**dict(r)) for r in store.db.execute(sql + " ORDER BY lap_time", args)]


def delete_reference(store: Store, ref_id: int) -> None:
    row = get_reference(store, ref_id)
    (store.data_dir / row.npz_path).unlink(missing_ok=True)
    store.db.execute("DELETE FROM refs WHERE id=?", (ref_id,))
    store.db.commit()
