"""Diagnostic de comportement (sous/survirage par phase de virage) et de pneus.

Le pilote applique lui-même les réglages (setups iRacing chiffrés) : ici on ne fait que
mesurer et proposer. Les indices sont des indicateurs relatifs, surtout utiles en avant/après.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from simengineer.coach.corners import Corner
from simengineer.core.config import SetupConfig
from simengineer.telemetry.resample import GridLap

PHASES = ("entry", "mid", "exit")
_MID_HALF_M = 12.0


@dataclass(frozen=True, slots=True)
class PhaseBalance:
    understeer_index: float | None  # ~1 = neutre ; plus haut = plus de volant que la géométrie
    oversteer_events: float  # contre-braquages par tour
    samples: int


@dataclass(frozen=True, slots=True)
class Finding:
    problem: str  # understeer | oversteer | camber_high | camber_low | pressure_high | …
    where: str  # phase (entry/mid/exit) ou pneu (lf/rf/lr/rr) ou essieu (front/rear)
    value: float
    evidence: str = ""

    @property
    def key(self) -> str:
        return f"{self.problem}:{self.where}"


@dataclass(slots=True)
class Diagnosis:
    balance: dict[str, PhaseBalance] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    laps: int = 0


def _phase_masks(n: int, step: float, corners: list[Corner]) -> dict[str, np.ndarray[Any, Any]]:
    masks = {p: np.zeros(n, dtype=bool) for p in PHASES}
    for c in corners:
        s0, s1 = int(c.start_m / step), min(int(c.end_m / step), n - 1)
        a0 = max(s0, int((c.apex_m - _MID_HALF_M) / step))
        a1 = min(s1, int((c.apex_m + _MID_HALF_M) / step))
        masks["entry"][s0:a0] = True
        masks["mid"][a0 : a1 + 1] = True
        masks["exit"][a1 + 1 : s1 + 1] = True
    return masks


def lap_balance(grid: GridLap, corners: list[Corner], cfg: SetupConfig) -> dict[str, PhaseBalance]:
    """Indice de sous-virage et contre-braquages par phase, pour un tour."""
    if not grid.has("lat_accel"):
        return {}
    v, steer, a_lat = grid.ch("speed"), grid.ch("steering"), grid.ch("lat_accel")
    # Convention de signe volant/accélération : estimée sur le tour, pas supposée.
    conv = 1.0 if float(np.sum(steer * a_lat)) >= 0 else -1.0
    loaded = (np.abs(a_lat) > 4.0) & (v > 15.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        neutral = cfg.wheelbase_m * np.abs(a_lat) / np.maximum(v, 1.0) ** 2  # angle de roue neutre
        index = (np.abs(steer) / cfg.steering_ratio) / neutral
    counter = (steer * a_lat * conv < 0) & (np.abs(a_lat) > 3.0) & (np.abs(steer) > 0.03)
    starts = counter & ~np.roll(counter, 1)
    out = {}
    for phase, mask in _phase_masks(grid.n, grid.step, corners).items():
        m = mask & loaded
        idx = float(np.median(index[m])) if m.any() else None
        out[phase] = PhaseBalance(idx, float(np.count_nonzero(starts & mask)), int(m.sum()))
    return out


def average_balance(per_lap: list[dict[str, PhaseBalance]]) -> dict[str, PhaseBalance]:
    out = {}
    for phase in PHASES:
        items = [b[phase] for b in per_lap if phase in b]
        idx = [b.understeer_index for b in items if b.understeer_index is not None]
        if not items:
            continue
        out[phase] = PhaseBalance(
            statistics.median(idx) if idx else None,
            statistics.fmean(b.oversteer_events for b in items),
            sum(b.samples for b in items),
        )
    return out


def balance_findings(balance: dict[str, PhaseBalance], cfg: SetupConfig) -> list[Finding]:
    out = []
    for phase, b in balance.items():
        if b.oversteer_events >= cfg.oversteer_events_per_lap:
            out.append(Finding("oversteer", phase, round(b.oversteer_events, 1)))
        elif b.understeer_index is not None and b.understeer_index > cfg.understeer_index:
            out.append(Finding("understeer", phase, round(b.understeer_index, 2)))
    return out


def tyre_findings(
    temps_c: dict[str, tuple[float, float, float]],
    cfg: SetupConfig,
) -> list[Finding]:
    """Températures gauche/milieu/droite par pneu (lues au stand).

    Hypothèse à vérifier : l'ordre est gauche -> droite vu de derrière ; le bord intérieur
    est donc à droite pour les pneus gauches et à gauche pour les pneus droits.
    """
    out = []
    avg: dict[str, float] = {}
    for tyre, (left, mid, right) in temps_c.items():
        inner, outer = (right, left) if tyre[0] == "l" else (left, right)
        spread = inner - outer
        avg[tyre] = (left + mid + right) / 3.0
        if spread > cfg.camber_spread_max_c:
            out.append(Finding("camber_high", tyre, round(spread, 1)))
        elif spread < cfg.camber_spread_min_c:
            out.append(Finding("camber_low", tyre, round(spread, 1)))
        bulge = mid - (inner + outer) / 2.0
        if bulge > cfg.pressure_middle_delta_c:
            out.append(Finding("pressure_high", tyre, round(bulge, 1)))
        elif bulge < -cfg.pressure_middle_delta_c:
            out.append(Finding("pressure_low", tyre, round(bulge, 1)))
    if {"lf", "rf", "lr", "rr"} <= avg.keys():
        front, rear = (avg["lf"] + avg["rf"]) / 2.0, (avg["lr"] + avg["rr"]) / 2.0
        if front - rear > 10.0:
            out.append(Finding("axle_hot", "front", round(front - rear, 1)))
        elif rear - front > 10.0:
            out.append(Finding("axle_hot", "rear", round(rear - front, 1)))
    return out


def diagnose(
    laps: list[GridLap],
    corners: list[Corner],
    cfg: SetupConfig,
    temps_c: dict[str, tuple[float, float, float]] | None = None,
) -> Diagnosis:
    """Diagnostic sur des tours PROPRES (la moyenne gomme les erreurs de pilotage isolées)."""
    balance = average_balance([lap_balance(g, corners, cfg) for g in laps])
    findings = balance_findings(balance, cfg)
    if temps_c:
        findings += tyre_findings(temps_c, cfg)
    return Diagnosis(balance, findings, len(laps))
