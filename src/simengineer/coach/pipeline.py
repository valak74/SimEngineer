"""Chaîne d'analyse d'un tour (exécutée dans le processus à basse priorité)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from simengineer.coach.advice import Advice, CornerDelta, advise, compare_laps
from simengineer.coach.corners import Corner, detect_corners
from simengineer.core.config import CoachConfig
from simengineer.telemetry.laps import Lap
from simengineer.telemetry.resample import GridLap, resample_lap, save_lap_npz
from simengineer.voice.arbiter import Priority, Utterance

if TYPE_CHECKING:
    from simengineer.store.db import Store


@dataclass(slots=True)
class LapAnalysis:
    lap_number: int
    lap_time: float
    ref_time: float
    ref_label: str
    deltas: list[CornerDelta]
    advice: list[Advice]

    @property
    def delta_s(self) -> float:
        return self.lap_time - self.ref_time

    def top(self, n: int) -> list[Advice]:
        return self.advice[:n]


@dataclass(slots=True)
class LapResult:
    lap: Lap
    grid: GridLap
    corners: list[Corner] = field(default_factory=list)
    analysis: LapAnalysis | None = None
    npz_path: str | None = None


def match_grid(ref: GridLap, like: GridLap) -> GridLap:
    """Ramène une référence sur la grille d'un tour (longueurs de piste légèrement différentes)."""
    if ref.n == like.n:
        return ref
    x_new = np.linspace(0.0, 1.0, like.n, endpoint=False)
    x_old = np.linspace(0.0, 1.0, ref.n, endpoint=False)
    channels = {k: np.interp(x_new, x_old, v) for k, v in ref.channels.items()}
    return GridLap(like.track_length_m, ref.lap_time, channels, like.step, dict(ref.meta))


def analyze_lap(
    grid: GridLap,
    ref: GridLap,
    corners: list[Corner],
    cfg: CoachConfig,
    lang: str = "fr",
    ref_label: str = "",
) -> LapAnalysis:
    ref = match_grid(ref, grid)
    deltas = compare_laps(grid, ref, corners)
    number = int(grid.meta.get("number", 0))
    return LapAnalysis(
        number, grid.lap_time, ref.lap_time, ref_label, deltas, advise(deltas, cfg, lang)
    )


def process_lap(
    lap: Lap,
    ref: GridLap | None,
    corners: list[Corner] | None,
    cfg: CoachConfig,
    lang: str = "fr",
    ref_label: str = "",
    npz_file: Path | None = None,
    npz_rel: str | None = None,
) -> LapResult:
    """Rééchantillonne, enregistre le NPZ et compare à la référence si le tour est valide."""
    grid = resample_lap(lap)
    if npz_file is not None:
        save_lap_npz(npz_file, grid, lap)
    result = LapResult(lap, grid, list(corners or []), npz_path=npz_rel)
    if not lap.valid:
        return result
    if not result.corners:
        result.corners = detect_corners(match_grid(ref, grid) if ref else grid, cfg)
    if ref is not None and result.corners:
        result.analysis = analyze_lap(grid, ref, result.corners, cfg, lang, ref_label)
    return result


def debrief_utterances(analysis: LapAnalysis, cfg: CoachConfig) -> list[Utterance]:
    """Débrief court entre deux tours : 1-2 conseils, dits en ligne droite (P2)."""
    return [
        Utterance(Priority.P2, a.key, a.short, ttl_s=45.0, cooldown_s=150.0)
        for a in analysis.top(cfg.max_advice)
    ]


def corner_rows(analysis: LapAnalysis) -> list[tuple[int, float, str, dict[str, object]]]:
    """Lignes à stocker : (virage, perte, défaut principal, métriques du tour)."""
    issue_by_corner = {a.corner: a.issue for a in analysis.advice}
    return [
        (d.corner.number, d.loss_s, issue_by_corner.get(d.corner.number, ""), d.lap.to_dict())
        for d in analysis.deltas
    ]


def reanalyze_session(
    store: Store, session_id: int, cfg: CoachConfig, lang: str = "fr"
) -> tuple[str, list[LapAnalysis]]:
    """Recalcule l'analyse de chaque tour valide d'une session stockée.

    Référence : tour importé pour ce circuit/voiture, sinon meilleur tour de la session.
    Renvoie (libellé de la référence, analyses) ; le tour de référence lui-même est omis.
    """
    from simengineer.reference.select import select_reference

    sess = store.session(session_id)
    rows = [r for r in store.laps(session_id, valid_only=True) if r.npz_path]
    if not rows:
        return "", []
    grids = {r.number: store.load_grid(r.npz_path or "") for r in rows}
    chosen = select_reference(store, sess.track, sess.car)
    skip: int | None = None
    if chosen is not None:
        label, ref = chosen[0].label, chosen[1]
    else:
        best = min(rows, key=lambda r: r.lap_time)
        label, ref, skip = f"tour {best.number}", grids[best.number], best.number
    from simengineer.coach.trackmap import load_corners

    corners = load_corners(store, sess.track, sess.track_length_m) or detect_corners(ref, cfg)
    return label, [
        analyze_lap(grid, ref, corners, cfg, lang, label)
        for number, grid in grids.items()
        if number != skip
    ]
