"""Économie de carburant chiffrée : objectif par tour et lever de pied par virage.

Calcul entièrement en code (règle 3) à partir d'un tour propre sur grille de 1 m :
- débit supposé proportionnel à (ralenti + accélérateur) le long de la distance, étalonné
  sur la consommation mesurée du tour (hypothèse à vérifier, docs/ASSUMPTIONS.md) ;
- lever de pied = roue libre de L mètres avant le point de freinage : carburant économisé
  ≈ débit plein gaz × L, temps perdu estimé avec une décélération de roue libre (estimation
  prudente : le freinage plus tardif qui suit n'est pas compté).
On choisit d'abord les leviers les plus rentables (litres gagnés par seconde perdue).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from simengineer.coach.corners import Corner
from simengineer.core.types import FloatArray
from simengineer.engineer.fuel import FuelStatus
from simengineer.telemetry.resample import GridLap

IDLE_SHARE = 0.08  # part du débit au ralenti (hypothèse)
COAST_DECEL = 1.6  # m/s^2 en roue libre (frein moteur + aéro, hypothèse)
STEP_M = 10.0
MAX_LIFT_M = 150.0
MAX_CORNERS = 4
MIN_STRAIGHT_KEEP_M = 60.0  # on ne lève jamais juste à la sortie du virage précédent


@dataclass(frozen=True, slots=True)
class Lift:
    corner: int
    label: str
    lift_m: float
    save_l: float  # par tour
    cost_s: float  # par tour


@dataclass(slots=True)
class FuelSavePlan:
    shortfall_l: float  # carburant manquant pour finir (marge comprise)
    laps_remaining: float
    current_per_lap_l: float
    target_per_lap_l: float
    lifts: list[Lift] = field(default_factory=list)

    @property
    def save_per_lap_l(self) -> float:
        return sum(lift.save_l for lift in self.lifts)

    @property
    def cost_per_lap_s(self) -> float:
        return sum(lift.cost_s for lift in self.lifts)

    @property
    def feasible(self) -> bool:
        return self.save_per_lap_l + 1e-9 >= self.current_per_lap_l - self.target_per_lap_l


def burn_weights(grid: GridLap) -> FloatArray:
    return np.asarray(IDLE_SHARE + (1.0 - IDLE_SHARE) * np.clip(grid.ch("throttle"), 0, 1))


def full_throttle_rate(grid: GridLap, fuel_per_lap_l: float) -> float:
    """Litres par mètre à plein gaz, étalonnés sur la consommation mesurée du tour."""
    return fuel_per_lap_l / (float(burn_weights(grid).sum()) * grid.step)


def _brake_point(grid: GridLap, c: Corner) -> int:
    step = grid.step
    s0, apex = int(c.zone_start_m / step), min(int(c.apex_m / step), grid.n - 1)
    on = np.flatnonzero(grid.ch("brake")[s0 : apex + 1] > 0.1)
    return s0 + int(on[0]) if on.size else int(c.start_m / step)


def lift_curve(
    grid: GridLap, c: Corner, prev_end_m: float, rate: float
) -> list[tuple[float, float, float]]:
    """(lever en m, litres économisés, secondes perdues) par pas de 10 m pour un virage."""
    step, n = grid.step, grid.n
    bp = _brake_point(grid, c)
    room = (bp * step - prev_end_m) % grid.track_length_m - MIN_STRAIGHT_KEEP_M
    max_lift = min(MAX_LIFT_M, room)
    v, w = grid.ch("speed"), burn_weights(grid)
    out = []
    lift = STEP_M
    while lift <= max_lift + 1e-9:
        k = int(lift / step)
        idx = (bp - k + np.arange(k)) % n
        save = rate * float((w[idx] - IDLE_SHARE).sum()) * step
        v0 = float(v[idx[0]])
        x = np.arange(1, k + 1) * step
        v_coast = np.sqrt(np.maximum(v0 * v0 - 2.0 * COAST_DECEL * x, 1.0))
        v_coast = np.minimum(v_coast, v[idx])
        cost = float(np.sum(step / v_coast - step / np.maximum(v[idx], 1.0)))
        out.append((lift, save, cost))
        lift += STEP_M
    return out


def plan_fuel_save(
    grid: GridLap,
    corners: list[Corner],
    fuel: FuelStatus,
    max_save_share: float = 0.10,
    margin_laps: float = 1.0,
    save_reserve_l: float = 0.5,
    need_per_lap_l: float | None = None,
) -> FuelSavePlan | None:
    """Plan pour finir sans arrêt, ou None si inutile, impossible ou données insuffisantes."""
    if (
        fuel.avg_per_lap_l is None
        or fuel.laps_remaining is None
        or fuel.fuel_to_add_l is None
        or (fuel.fuel_to_add_l <= 0.0 and need_per_lap_l is None)
        or fuel.laps_remaining < 1.0
        or not corners
    ):
        return None
    avg = fuel.avg_per_lap_l
    if need_per_lap_l is not None:  # renfort : écart réel à combler, déjà corrigé
        need_per_lap = need_per_lap_l
        shortfall = need_per_lap * fuel.laps_remaining
    else:
        # En économie on vise l'arrivée avec une petite réserve, pas avec un tour de marge.
        shortfall = fuel.fuel_to_add_l - margin_laps * avg + save_reserve_l
        need_per_lap = shortfall / fuel.laps_remaining
    if need_per_lap <= 0.0:
        return None
    if need_per_lap > max_save_share * avg:
        return None  # trop d'écart : il faut s'arrêter (fenêtre de stand)
    rate = full_throttle_rate(grid, avg)
    curves = {}
    for i, c in enumerate(corners):
        prev_end = corners[i - 1].end_m if i > 0 else corners[-1].end_m - grid.track_length_m
        curve = lift_curve(grid, c, prev_end, rate)
        if curve:
            curves[c.number] = (c, curve)
    chosen: dict[int, int] = {}  # virage -> nombre de pas retenus
    saved = 0.0
    while saved < need_per_lap:
        best: tuple[float, int] | None = None
        for num, (_c, curve) in curves.items():
            k = chosen.get(num, 0)
            if k >= len(curve) or (k == 0 and len(chosen) >= MAX_CORNERS):
                continue
            prev_save, prev_cost = (curve[k - 1][1], curve[k - 1][2]) if k else (0.0, 0.0)
            gain, cost = curve[k][1] - prev_save, curve[k][2] - prev_cost
            ratio = gain / max(cost, 1e-6)
            if best is None or ratio > best[0]:
                best = (ratio, num)
        if best is None:
            break
        num = best[1]
        k = chosen.get(num, 0)
        prev = curves[num][1][k - 1][1] if k else 0.0
        saved += curves[num][1][k][1] - prev
        chosen[num] = k + 1
    lifts = []
    for num, k in sorted(chosen.items()):
        c, curve = curves[num]
        lift_m, save, cost = curve[k - 1]
        lifts.append(Lift(num, c.label, lift_m, save, cost))
    plan = FuelSavePlan(
        shortfall_l=shortfall,
        laps_remaining=fuel.laps_remaining,
        current_per_lap_l=avg,
        target_per_lap_l=avg - need_per_lap,
        lifts=lifts,
    )
    return plan if plan.feasible else None


@dataclass(frozen=True, slots=True)
class SaveCheck:
    key: str  # gabarit
    params: dict[str, object]


def check_lap(plan: FuelSavePlan, used_l: float, tolerance_l: float = 0.02) -> SaveCheck:
    """Verdict après un tour en mode économie."""
    if not math.isfinite(used_l) or used_l <= 0:
        return SaveCheck("", {})
    over = used_l - plan.target_per_lap_l
    if over <= tolerance_l:
        return SaveCheck("fuel_save_ok", {"used": used_l})
    return SaveCheck("fuel_save_more", {"used": used_l, "target": plan.target_per_lap_l})
