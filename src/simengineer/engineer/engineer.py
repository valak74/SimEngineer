"""Ingénieur de course : agrège carburant, stand, pneus, météo, écarts.

Travail par tour et à 1 Hz seulement. Produit des énoncés P1 par gabarits et un
instantané `RaceState` que les outils du LLM lisent (le LLM ne calcule rien).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from simengineer.core.config import EngineerConfig
from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import FULL_COURSE_YELLOW_MASK
from simengineer.engineer.fuel import FuelModel, FuelStatus, race_laps_remaining
from simengineer.engineer.gaps import Gaps, compute_gaps
from simengineer.engineer.pit import PitWindow, pit_window
from simengineer.engineer.pit_commands import DisabledPitCommander, PitCommander
from simengineer.engineer.tyres import TyreModel, TyreStatus
from simengineer.engineer.weather import WeatherMonitor, WeatherStatus
from simengineer.telemetry.laps import Lap
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import fmt_delta, fmt_lap_time, fmt_num, render


@dataclass(slots=True)
class RaceState:
    """Instantané de course déjà calculé. Seule source de chiffres pour le dialogue."""

    session_type: str = "Practice"
    lap: int = 0
    fuel: FuelStatus = field(default_factory=lambda: FuelStatus(math.nan, 0))
    pit: PitWindow = field(default_factory=lambda: PitWindow("unknown"))
    tyres: TyreStatus = field(default_factory=lambda: TyreStatus(0))
    weather: WeatherStatus = field(default_factory=WeatherStatus)
    gaps: Gaps = field(default_factory=Gaps)
    last_lap_time: float | None = None
    best_lap_time: float | None = None
    laps_completed: int = 0
    valid_laps: int = 0
    under_yellow: bool = False


class RaceEngineer:
    def __init__(
        self,
        cfg: EngineerConfig,
        info: SessionInfo,
        lang: str = "fr",
        commander: PitCommander | None = None,
    ) -> None:
        self.cfg = cfg
        self.info = info
        self.lang = lang
        self.commander: PitCommander = commander or DisabledPitCommander()
        self.fuel = FuelModel(cfg.fuel_window_laps, cfg.fuel_margin_laps, cfg.fuel_reserve_l)
        self.tyres = TyreModel(cfg.tyre_wear_warning_pct)
        self.weather = WeatherMonitor(cfg.track_temp_delta_c)
        self.state = RaceState(session_type=info.session_type)
        self._last_second = -1
        self._yellow_since: float | None = None
        self._said: set[str] = set()  # annonces uniques (par arrêt / par neutralisation)
        self._last_frame: Frame | None = None

    # --- sortie
    def _utt(self, key: str, dedupe: str, cooldown_s: float = 60.0, **params: Any) -> Utterance:
        return Utterance(
            Priority.P1, dedupe, render(key, self.lang, **params), cooldown_s=cooldown_s
        )

    def _n(self, value: float, decimals: int = 1) -> str:
        return fmt_num(value, decimals, self.lang)

    # --- boucle
    def on_frame(self, f: Frame) -> list[Utterance]:
        """À appeler à chaque image ; ne travaille qu'une fois par seconde."""
        self._last_frame = f
        second = int(f.session_time)
        if second == self._last_second and f.slow is None:
            return []
        self._last_second = second
        out: list[Utterance] = []
        st = self.state
        st.lap = f.lap
        fcy = bool(f.session_flags & FULL_COURSE_YELLOW_MASK)
        st.under_yellow = fcy
        if fcy and self._yellow_since is None:
            self._yellow_since = f.session_time
        elif not fcy:
            self._yellow_since = None
            self._said.discard("pit_now_yellow")
        if f.slow is not None:
            self.tyres.on_slow(f.slow)
            for key, params in self.weather.on_slow(f.slow):
                out.append(self._utt(key, key, 120.0, **params))
            st.weather = self.weather.status()
            st.tyres = self.tyres.status()
        self._refresh(f)
        long_yellow = (
            self._yellow_since is not None
            and f.session_time - self._yellow_since >= self.cfg.long_yellow_min_s
        )
        if long_yellow and st.pit.is_open and "pit_now_yellow" not in self._said:
            # Jaune longue : l'arrêt coûte moins de temps, on le propose une fois.
            self._said.add("pit_now_yellow")
            st.pit = self._window(f, long_yellow=True)
            liters = math.ceil(st.pit.fuel_to_add_l)
            out.append(self._utt("pit_now_yellow", "pit_now_yellow", 120.0, liters=liters))
        return out

    def _window(self, f: Frame, long_yellow: bool = False) -> PitWindow:
        return pit_window(
            self.state.fuel,
            self.info.fuel_capacity_l,
            f.lap,
            f.lap_dist_pct,
            self.cfg.fuel_margin_laps,
            long_yellow,
        )

    def _refresh(self, f: Frame) -> None:
        st = self.state
        remaining = race_laps_remaining(f, self.info, self.fuel.avg_lap_time)
        st.fuel = self.fuel.status(f.fuel_level, remaining)
        st.pit = self._window(f)
        st.gaps = compute_gaps(f, self.fuel.avg_lap_time or st.best_lap_time)

    def on_lap(self, lap: Lap) -> list[Utterance]:
        out: list[Utterance] = []
        st = self.state
        st.laps_completed += 1
        was_known = self.fuel.avg_per_lap is not None
        self.fuel.add_lap(lap)
        self.tyres.on_lap(lap.pit)
        st.tyres = self.tyres.status()
        if lap.pit:
            self._said.clear()  # nouvel arrêt effectué : les annonces de stand repartent
        if lap.complete and not lap.pit:
            out.append(self._lap_time_call(lap))
        f = self._last_frame
        if f is None:
            return out
        self._refresh(f)
        fuel, pit = st.fuel, st.pit
        if fuel.avg_per_lap_l is None or fuel.laps_in_tank is None:
            return out
        laps = self._n(fuel.laps_in_tank)
        if fuel.laps_remaining is None:
            if fuel.laps_in_tank <= self.cfg.low_fuel_laps_warning:
                out.append(self._utt("fuel_low", "fuel_low", 0.0, laps=laps))
            elif not was_known:
                out.append(self._utt("fuel_laps", "fuel_first", laps=laps))
            return out
        if not was_known:
            out.append(
                self._utt(
                    "fuel_status", "fuel_first", laps=laps, remaining=self._n(fuel.laps_remaining)
                )
            )
        liters = math.ceil(pit.fuel_to_add_l)
        if pit.advice == "last_chance":
            out.append(self._utt("pit_last_chance", "pit_last_chance", 0.0))
        elif pit.advice == "open" and "pit_open" not in self._said:
            self._said.add("pit_open")
            out.append(self._utt("pit_window_open", "pit_open", liters=liters))
        elif pit.advice == "wait" and "pit_wait" not in self._said and pit.open_lap is not None:
            self._said.add("pit_wait")
            out.append(self._utt("pit_window_in", "pit_wait", laps=pit.open_lap - lap.number))
        elif pit.advice == "no_stop" and was_known and "fuel_ok" not in self._said:
            self._said.add("fuel_ok")
            out.append(self._utt("fuel_ok", "fuel_ok", margin=self._n(fuel.margin_laps or 0.0)))
        tyres = st.tyres
        if (
            tyres.wear_pct is not None
            and tyres.wear_pct <= self.cfg.tyre_wear_warning_pct
            and "tyres_worn" not in self._said
        ):
            self._said.add("tyres_worn")
            out.append(self._utt("tyres_worn", "tyres_worn", wear=round(tyres.wear_pct)))
        return out

    def _lap_time_call(self, lap: Lap) -> Utterance:
        st = self.state
        st.last_lap_time = lap.lap_time
        text_time = fmt_lap_time(lap.lap_time, self.lang)
        best = st.best_lap_time
        if lap.valid:
            st.valid_laps += 1
        if lap.valid and (best is None or lap.lap_time < best):
            st.best_lap_time = lap.lap_time
            return self._utt("lap_best", "lap_time", 0.0, time=text_time)
        if best is None or abs(lap.lap_time - best) < 0.005:
            return self._utt("lap_time", "lap_time", 0.0, time=text_time)
        delta = fmt_delta(lap.lap_time - best, self.lang)
        return self._utt("lap_time_delta", "lap_time", 0.0, time=text_time, delta=delta)

    # --- commandes de stand (désactivées par défaut)
    def set_pit_fuel(self, liters: float) -> str:
        res = self.commander.set_fuel(liters)
        return render(res.key, self.lang, **res.params)

    def set_pit_tyres(self, change: bool) -> str:
        res = self.commander.set_tyres(change)
        return render(res.key, self.lang, **res.params)
