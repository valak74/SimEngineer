"""Fenêtre de stand : calcul pur à partir de l'état carburant."""

from __future__ import annotations

import math
from dataclasses import dataclass

from simengineer.engineer.fuel import FuelStatus


@dataclass(frozen=True, slots=True)
class PitWindow:
    advice: str  # no_stop | unknown | wait | open | last_chance | pit_now_yellow
    stops_needed: int = 0
    is_open: bool = False
    open_in_laps: float | None = None  # tours d'ici l'ouverture (0 = ouverte)
    close_in_laps: float | None = None  # tours d'ici la panne sèche
    open_lap: int | None = None  # premier tour à la fin duquel l'arrêt permet de finir
    close_lap: int | None = None  # dernier tour à la fin duquel on peut encore rentrer
    fuel_to_add_l: float = 0.0  # à ajouter lors du prochain arrêt


def pit_window(
    fuel: FuelStatus,
    capacity_l: float,
    current_lap: int,
    lap_pct: float,
    margin_laps: float,
    long_yellow: bool = False,
) -> PitWindow:
    """Fenêtre du prochain arrêt.

    Ouverture : premier moment où un plein permet de finir avec les arrêts restants.
    Fermeture : dernier tour bouclable avec le carburant actuel.
    """
    if fuel.can_finish is None or fuel.avg_per_lap_l is None or fuel.laps_remaining is None:
        return PitWindow("unknown")
    if fuel.can_finish:
        return PitWindow("no_stop")
    avg = fuel.avg_per_lap_l
    need = fuel.fuel_to_add_l or 0.0
    cap = capacity_l if capacity_l > 0 else math.inf
    stops = max(1, math.ceil(need / cap)) if math.isfinite(cap) else 1
    close_in = fuel.laps_in_tank or 0.0
    open_in = max(0.0, fuel.laps_remaining + margin_laps - stops * cap / avg)
    pos = current_lap + lap_pct  # position actuelle en « tours »
    open_lap = max(current_lap, math.ceil(pos + open_in - 1.0 - 1e-9))
    close_lap = math.floor(pos + close_in - 1.0 + 1e-9)
    is_open = open_lap <= current_lap
    to_add = min(need, max(cap - fuel.fuel_l, 0.0))
    if close_lap <= current_lap:
        advice = "last_chance"
    elif long_yellow and is_open:
        advice = "pit_now_yellow"
    elif is_open:
        advice = "open"
    else:
        advice = "wait"
    return PitWindow(
        advice=advice,
        stops_needed=stops,
        is_open=is_open,
        open_in_laps=open_in,
        close_in_laps=close_in,
        open_lap=open_lap,
        close_lap=max(close_lap, current_lap),
        fuel_to_add_l=to_add,
    )
