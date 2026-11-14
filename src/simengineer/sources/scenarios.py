"""Scénarios synthétiques nommés (tests, démonstration, mesure de performance)."""

from __future__ import annotations

from collections.abc import Callable

from simengineer.core.vars import Flags, TrackSurface
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    OtherCar,
    Scenario,
    build_lap_profile,
    lap_style,
)

LAP_TIME = build_lap_profile(DEFAULT_TRACK).lap_time


def clean() -> Scenario:
    """Essais libres, tours propres."""
    return Scenario(n_laps=5)


def stopped_car() -> Scenario:
    """Voiture arrêtée en piste à 1250 m de la ligne (300 m devant quand le pilote est à 950 m)."""
    return Scenario(n_laps=2, others=(OtherCar(idx=5, gap_m=1250.0, pace=0.0),))


def overtake() -> Scenario:
    """Une voiture plus lente devant, doublée par la gauche (elle passe donc à droite)."""
    return Scenario(n_laps=2, others=(OtherCar(idx=7, gap_m=60.0, pace=0.97, side="right"),))


def accident() -> Scenario:
    """Deux voitures s'arrêtent devant (une en piste, une hors-piste) + drapeau jaune."""
    return Scenario(
        n_laps=2,
        others=(
            OtherCar(idx=3, gap_m=900.0, pace=1.0, stop_at_s=20.0),
            OtherCar(
                idx=4,
                gap_m=930.0,
                pace=1.0,
                stop_at_s=20.5,
                stopped_surface=int(TrackSurface.OFF_TRACK),
            ),
        ),
        flag_periods=((21.0, 60.0, int(Flags.YELLOW_WAVING)),),
    )


def race() -> Scenario:
    """Course de 12 tours avec trafic, jaune, hors-piste et défauts de pilotage variés."""
    slow_t3 = lap_style(c2=CornerStyle(brake_offset_m=-14.0), c6=CornerStyle(throttle_offset_m=12))
    low_vmin = lap_style(c4=CornerStyle(min_speed_delta=-1.5), c2=CornerStyle(brake_offset_m=-10))
    return Scenario(
        n_laps=12,
        session_type="Race",
        race_laps=12,
        fuel_start_l=26.0,
        fuel_per_lap_l=2.5,
        base_style=slow_t3,
        lap_styles=((3, low_vmin), (5, ()), (8, low_vmin)),
        others=(
            OtherCar(idx=7, gap_m=80.0, pace=0.985, side="right"),
            OtherCar(idx=9, gap_m=-40.0, pace=1.004, side="left"),
            OtherCar(idx=5, gap_m=2500.0, pace=1.0, stop_at_s=3.2 * LAP_TIME),
        ),
        flag_periods=((3.25 * LAP_TIME, 3.9 * LAP_TIME, int(Flags.YELLOW_WAVING)),),
        offtracks=((6, 0.36, 0.38),),
        incidents=((6, 0.36, 1),),
        track_temp_drift_c=4.0,
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "clean": clean,
    "stopped_car": stopped_car,
    "overtake": overtake,
    "accident": accident,
    "race": race,
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]()
    except KeyError:
        raise ValueError(
            f"Scénario inconnu : {name!r} (disponibles : {', '.join(sorted(SCENARIOS))})"
        ) from None
