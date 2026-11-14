"""Sélection automatique du tour de référence (même circuit, même voiture)."""

from __future__ import annotations

from simengineer.reference.store import RefRow, list_references
from simengineer.store.db import Store
from simengineer.telemetry.resample import GridLap


def select_reference(store: Store, track: str, car: str) -> tuple[RefRow, GridLap] | None:
    """Référence la plus rapide importée pour ce circuit ET cette voiture, sinon None."""
    rows = list_references(store, track=track, car=car)
    if not rows:
        return None
    best = min(rows, key=lambda r: r.lap_time)
    return best, store.load_grid(best.npz_path)
