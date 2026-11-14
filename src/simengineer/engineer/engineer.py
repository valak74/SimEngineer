"""Ingénieur de course : agrège carburant, stand, pneus, météo, écarts.

Travail par tour et à 1 Hz seulement. Produit des énoncés P1 par gabarits et un
instantané `RaceState` que les outils du LLM lisent (le LLM ne calcule rien).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from simengineer.coach.corners import Corner
from simengineer.core.config import EngineerConfig
from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import FULL_COURSE_YELLOW_MASK
from simengineer.engineer.fuel import FuelModel, FuelStatus, race_laps_remaining
from simengineer.engineer.fuel_save import FuelSavePlan, plan_fuel_save
from simengineer.engineer.gaps import Gaps, compute_gaps
from simengineer.engineer.pit import PitWindow, pit_window
from simengineer.engineer.pit_commands import DisabledPitCommander, PitCommander
from simengineer.engineer.race import History, RaceMonitor, RaceStatus
from simengineer.engineer.tyres import TyreModel, TyreStatus
from simengineer.engineer.weather import WeatherMonitor, WeatherStatus
from simengineer.telemetry.laps import Lap
from simengineer.telemetry.resample import GridLap
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import corner_word, fmt_delta, fmt_lap_time, fmt_num, render


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
    fuel_save: FuelSavePlan | None = None
    race: RaceStatus = field(default_factory=RaceStatus)


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
        self.race = RaceMonitor(
            info, lang, None, lambda: self.state.best_lap_time,
            margin_laps=cfg.fuel_margin_laps, reserve_l=cfg.fuel_reserve_l,
        )  # fmt: skip
        self.state.race = self.race.status
        self._save_base: GridLap | None = None  # tour à l'attaque ayant servi au plan
        self._save_target_lap: float | None = None
        self._save_misses = 0
        self._last_save_used: float | None = None

    # --- sortie
    def _utt(self, key: str, dedupe: str, cooldown_s: float = 60.0, **params: Any) -> Utterance:
        return Utterance(
            Priority.P1, dedupe, render(key, self.lang, **params), cooldown_s=cooldown_s
        )

    def _n(self, value: float, decimals: int = 1) -> str:
        return fmt_num(value, decimals, self.lang)

    # --- boucle
    def set_history(self, history: History | None) -> None:
        self.race.history = history

    def set_live_delta(self, provider: Callable[[], float | None]) -> None:
        self.race._live_delta = provider

    def on_frame(self, f: Frame) -> list[Utterance]:
        """À appeler à chaque image ; calculs lourds une fois par seconde seulement."""
        self._last_frame = f
        out: list[Utterance] = self.race.on_frame(f)
        second = int(f.session_time)
        if second == self._last_second and f.slow is None:
            return out
        self._last_second = second
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
        out += self._fuel_save_check(lap)
        out += self.race.on_lap(lap, self._last_frame)
        if self.race.status.finished:
            return out  # course terminée : seul le débrief compte
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
        if pit.advice == "last_chance" and self._saving_holds():
            pass  # le rythme économisé suffit pour finir : pas d'alarme
        elif pit.advice == "last_chance":
            out.append(self._utt("pit_last_chance", "pit_last_chance", 0.0))
        elif st.fuel_save is not None or self.save_possible():
            pass  # économie possible : pas d'invitation à s'arrêter (« ce tour ! » reste actif)
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

    # --- économie de carburant
    def save_possible(self) -> bool:
        """Finir sans s'arrêter est-il atteignable en économisant (calcul sans télémétrie) ?"""
        f, cfg = self.state.fuel, self.cfg
        if not self.info.is_race or f.avg_per_lap_l is None or f.laps_remaining is None:
            return False
        if f.fuel_to_add_l is None or f.laps_remaining < 1.0:
            return False
        short = f.fuel_to_add_l - cfg.fuel_margin_laps * f.avg_per_lap_l + cfg.fuel_save_reserve_l
        return 0.0 < short <= cfg.fuel_save_max_share * f.avg_per_lap_l * f.laps_remaining

    def _saving_holds(self) -> bool:
        """En économie : le dernier tour a-t-il été assez sobre pour finir à ce rythme ?"""
        target, used = self._save_target(), self._last_save_used
        return (
            self.state.fuel_save is not None
            and target is not None
            and used is not None
            and used <= target + 0.01
        )

    def _save_target(self) -> float | None:
        f = self.state.fuel
        if f.laps_remaining is None or f.laps_remaining < 0.5 or not math.isfinite(f.fuel_l):
            return None
        return (f.fuel_l - self.cfg.fuel_save_reserve_l) / f.laps_remaining

    def update_fuel_save(self, grid: GridLap, corners: list[Corner]) -> list[Utterance]:
        """Après chaque tour propre analysé : crée le plan, le renforce ou y met fin."""
        st, cfg = self.state, self.cfg
        if not self.info.is_race:
            return []
        plan = st.fuel_save
        target = self._save_target()
        need: float | None = None
        if plan is not None:
            attack = plan.current_per_lap_l  # conso à l'attaque mesurée avant le plan
            if target is not None and target >= attack:
                st.fuel_save = None
                self._save_misses = 0
                return [self._utt("fuel_save_done", "fuel_save_done", 120.0)]
            if target is None or target < attack * (1.0 - cfg.fuel_save_max_share):
                # Trop tard pour économiser assez : retour à la stratégie d'arrêt.
                st.fuel_save = None
                self._save_misses = 0
                self._said.discard("pit_open")
                liters = math.ceil(st.fuel.fuel_to_add_l or 0.0)
                return [self._utt("fuel_save_abort", "fuel_save_abort", 120.0, liters=liters)]
            if self._save_misses < 2 or self._save_base is None:
                return []
            # Deux tours de suite au-dessus de l'objectif : renfort, corrigé par l'efficacité
            # réellement mesurée (économie obtenue / économie prévue).
            planned = attack - plan.target_per_lap_l
            achieved = attack - (self._last_save_used or attack)
            efficiency = min(max(achieved / planned, 0.5), 1.5) if planned > 0 else 1.0
            need = (attack - target) / efficiency
            fuel = replace(st.fuel, avg_per_lap_l=attack)
            grid = self._save_base
            self._save_misses = 0
        else:
            if not self.save_possible():
                return []
            fuel = st.fuel
            self._save_base = grid
        new = plan_fuel_save(
            grid, corners, fuel, cfg.fuel_save_max_share * 1.5 if need else
            cfg.fuel_save_max_share, cfg.fuel_margin_laps, cfg.fuel_save_reserve_l, need,
        )  # fmt: skip
        if new is None:
            return []
        if plan is not None and {(x.corner, x.lift_m) for x in plan.lifts} == {
            (x.corner, x.lift_m) for x in new.lifts
        }:
            return []
        if plan is not None:
            new.current_per_lap_l = plan.current_per_lap_l
        st.fuel_save = new
        self._save_target_lap = target
        lifts = ", ".join(
            f"{round(x.lift_m)} mètres avant {corner_word(x.corner, self.lang, x.label)}"
            if self.lang == "fr"
            else f"{round(x.lift_m)} meters before {corner_word(x.corner, self.lang, x.label)}"
            for x in new.lifts
        )
        save = self._n(new.current_per_lap_l - new.target_per_lap_l, 2)
        return [self._utt("fuel_save_plan", "fuel_save_plan", 0.0, save=save, lifts=lifts)]

    def _fuel_save_check(self, lap: Lap) -> list[Utterance]:
        """Verdict du tour : conso réelle contre l'objectif fixé au début du tour."""
        plan, target = self.state.fuel_save, self._save_target_lap
        self._save_target_lap = self._save_target()
        if plan is None or target is None or not lap.clean_for_fuel:
            return []
        used = lap.fuel_used
        if not math.isfinite(used) or used <= 0:
            return []
        self._last_save_used = used
        if used <= target + 0.01:
            self._save_misses = 0
            return [self._utt("fuel_save_ok", "fuel_save_check", 0.0, used=self._n(used, 2))]
        self._save_misses += 1
        return [
            self._utt(
                "fuel_save_more",
                "fuel_save_check",
                0.0,
                used=self._n(used, 2),
                target=self._n(target, 2),
            )
        ]

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
    @property
    def pit_commands_available(self) -> bool:
        return not isinstance(self.commander, DisabledPitCommander)

    def set_pit_fuel(self, liters: float) -> str:
        res = self.commander.set_fuel(liters)
        return render(res.key, self.lang, **res.params)

    def set_pit_tyres(self, change: bool) -> str:
        res = self.commander.set_tyres(change)
        return render(res.key, self.lang, **res.params)
