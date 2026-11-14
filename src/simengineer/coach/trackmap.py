"""Carte des virages d'un circuit : détection automatique + fichier éditable par le pilote.

Le fichier `data/tracks/<circuit>.toml` (créé par `simengineer track export`) permet de
nommer les virages (« La Source »), de corriger leurs bornes, d'en fusionner ou d'en
supprimer. S'il existe, il remplace la détection automatique partout (coach, rappels,
interface, setup).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

from simengineer.coach.corners import Corner
from simengineer.core.log import get_logger
from simengineer.store.db import Store

log = get_logger("trackmap")


def _key(track: str) -> str:
    return f"trackmap:{track}"


def slug(track: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", track.strip()).strip("_").lower()
    return s or "circuit"


def track_file(data_dir: Path, track: str) -> Path:
    return data_dir / "tracks" / f"{slug(track)}.toml"


def with_zones(corners: list[Corner], length_m: float) -> list[Corner]:
    """Renumérote dans l'ordre du tour et recalcule les zones (partition complète du tour)."""
    ordered = sorted(corners, key=lambda c: c.apex_m)
    out = []
    for i, c in enumerate(ordered):
        z0 = 0.0 if i == 0 else (ordered[i - 1].end_m + c.start_m) / 2.0
        z1 = length_m if i == len(ordered) - 1 else (c.end_m + ordered[i + 1].start_m) / 2.0
        out.append(replace(c, number=i + 1, zone_start_m=z0, zone_end_m=z1))
    return out


class TrackFileError(ValueError):
    pass


def read_track_file(path: Path, length_m: float) -> list[Corner]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TrackFileError(f"{path} illisible : {exc}") from exc
    corners = []
    for i, raw in enumerate(data.get("corners", [])):
        try:
            start, apex, end = float(raw["start_m"]), float(raw["apex_m"]), float(raw["end_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrackFileError(
                f"{path} : virage n°{i + 1} incomplet (start_m/apex_m/end_m)"
            ) from exc
        if not 0 <= start <= apex <= end <= length_m:
            raise TrackFileError(
                f"{path} : virage n°{i + 1} : il faut 0 ≤ start_m ≤ apex_m ≤ end_m ≤ {length_m:.0f}"
            )
        corners.append(
            Corner(0, start, apex, end, 0.0, 0.0, int(raw.get("direction", 0)),
                   str(raw.get("label", "")).strip())
        )  # fmt: skip
    if not corners:
        raise TrackFileError(f"{path} : aucun virage ([[corners]])")
    return with_zones(corners, length_m)


def load_corners(store: Store, track: str, length_m: float | None = None) -> list[Corner] | None:
    """Fichier du pilote s'il existe et est valide, sinon carte détectée, sinon None."""
    stored = store.kv_get(_key(track))
    detected = [Corner.from_dict(c) for c in stored] if stored else None
    path = track_file(store.data_dir, track)
    if path.is_file():
        length = length_m or (detected[-1].zone_end_m if detected else None)
        if length is None:
            sessions = store.sessions(track=track)
            length = sessions[-1].track_length_m if sessions else None
        if length:
            try:
                return read_track_file(path, length)
            except TrackFileError as exc:
                log.error("%s — détection automatique utilisée à la place", exc)
    return detected


def save_detected(store: Store, track: str, corners: list[Corner]) -> None:
    store.kv_set(_key(track), [c.to_dict() for c in corners])


def export_track_file(store: Store, track: str, length_m: float, force: bool = False) -> Path:
    """Écrit le fichier éditable à partir de la carte actuelle (ne l'écrase pas sans force)."""
    path = track_file(store.data_dir, track)
    if path.exists() and not force:
        raise FileExistsError(f"{path} existe déjà (utilise --force pour le régénérer)")
    corners = load_corners(store, track, length_m)
    if not corners:
        raise TrackFileError(
            f"Aucune carte de virages pour {track} : roule d'abord un tour propre."
        )
    lines = [
        f"# Virages de « {track} » (longueur {length_m:.0f} m) — fichier éditable.",
        "# label    : nom prononcé si ≤ 3 mots (« La Source »), sinon « Virage N ».",
        "# start_m / apex_m / end_m : distances depuis la ligne (voir l'onglet Séances > Traces).",
        "# Supprime un bloc pour ignorer un virage ;",
        "# élargis les bornes pour fusionner une chicane.",
        "# Les virages sont renumérotés dans l'ordre du tour au chargement.",
        "",
    ]
    for c in corners:
        lines += [
            "[[corners]]",
            f"# {c.name}",
            f'label = "{c.label}"',
            f"start_m = {c.start_m:.0f}",
            f"apex_m = {c.apex_m:.0f}",
            f"end_m = {c.end_m:.0f}",
            f"direction = {c.direction}",
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def labels(corners: list[Corner] | None) -> dict[int, str]:
    return {c.number: c.label for c in corners or [] if c.label}


def corner_dicts(corners: list[Corner]) -> list[dict[str, Any]]:
    return [{**c.to_dict(), "name": c.name, "display": c.display} for c in corners]
