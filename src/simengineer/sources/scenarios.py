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
    gap_for_distance_at,
    lap_style,
)

LAP_TIME = build_lap_profile(DEFAULT_TRACK).lap_time


def clean() -> Scenario:
    """Essais libres, tours propres."""
    return Scenario(n_laps=5)


def stopped_car() -> Scenario:
    """Une voiture roule 300 m devant le pilote puis s'arrête net en piste à t = 10 s."""
    gap = gap_for_distance_at(DEFAULT_TRACK, 10.0, 300.0)
    return Scenario(n_laps=2, others=(OtherCar(idx=5, gap_m=gap, pace=1.0, stop_at_s=10.0),))


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


def multiclass() -> Scenario:
    """Une voiture d'une classe plus rapide (+15 %) arrive par derrière puis double à gauche."""
    return Scenario(
        n_laps=2,
        others=(
            OtherCar(idx=11, gap_m=-260.0, pace=1.15, side="left"),
            OtherCar(idx=12, gap_m=-120.0, pace=1.0, side="right"),  # même rythme : silence
        ),
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


def perf() -> Scenario:
    """Mesure de performance : 4 tours avec trafic dense (20 voitures) et un jaune."""
    cars = tuple(
        OtherCar(
            idx=i + 1,
            gap_m=-300.0 + 35.0 * i,
            pace=0.97 + 0.003 * i,
            side="left" if i % 2 else "right",
        )
        for i in range(20)
    )
    return Scenario(
        n_laps=4,
        session_type="Race",
        race_laps=20,
        others=cars,
        flag_periods=((60.0, 90.0, int(Flags.YELLOW_WAVING)),),
        base_style=lap_style(c2=CornerStyle(brake_offset_m=-12.0)),
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "perf": perf,
    "clean": clean,
    "stopped_car": stopped_car,
    "overtake": overtake,
    "accident": accident,
    "race": race,
    "multiclass": multiclass,
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]()
    except KeyError:
        raise ValueError(
            f"Scénario inconnu : {name!r} (disponibles : {', '.join(sorted(SCENARIOS))})"
        ) from None
