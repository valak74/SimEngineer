"""Outils exposés au LLM : lecture de données DÉJÀ calculées (règle 3) + commandes de stand.

Chaque appel est validé strictement par pydantic. Chaque résultat contient un champ
`say` : la phrase par gabarit, utilisée telle quelle en mode « tout local ».
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simengineer.coach.pipeline import LapAnalysis
from simengineer.engineer.engineer import RaceState
from simengineer.voice.templates import fmt_delta, fmt_lap_time, fmt_num, render


class EngineerLike(Protocol):
    state: RaceState

    @property
    def pit_commands_available(self) -> bool: ...

    def set_pit_fuel(self, liters: float) -> str: ...
    def set_pit_tyres(self, change: bool) -> str: ...


class CoachLike(Protocol):
    last_analysis: LapAnalysis | None
    delta_s: float | None


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArgs(_Args):
    pass


class CornerArgs(_Args):
    corner: int | None = Field(default=None, ge=1, le=60, description="Numéro du virage")


class PitFuelArgs(_Args):
    liters: float = Field(ge=0, le=300, description="Litres à ajouter au prochain arrêt")


class PitTyresArgs(_Args):
    change: bool = Field(description="true = changer les pneus, false = les garder")


@dataclass(frozen=True, slots=True)
class PendingPitCommand:
    kind: str  # "fuel" | "tyres"
    value: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    args: type[_Args]
    handler: Callable[[Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    ok: bool
    content: str  # JSON renvoyé au LLM
    data: dict[str, Any]

    @property
    def say(self) -> str:
        return str(self.data.get("say", ""))


def _r(value: float | None, decimals: int = 1) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, decimals)


class ToolRegistry:
    def __init__(
        self,
        engineer: EngineerLike,
        coach: CoachLike,
        lang: str = "fr",
        confirm: bool = True,
        confirm_timeout_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._eng = engineer
        self._coach = coach
        self.lang = lang
        self.confirm = confirm
        self._timeout = confirm_timeout_s
        self._clock = clock
        self._pending: PendingPitCommand | None = None
        specs = [
            ToolSpec("get_fuel_status", "Carburant : litres, tours possibles, tours restants, "
                     "litres à ajouter, marge.", NoArgs, self._fuel),
            ToolSpec("get_pit_window", "Fenêtre de stand : tours d'ouverture et de fermeture, "
                     "litres à ajouter, nombre d'arrêts.", NoArgs, self._pit),
            ToolSpec("get_lap_summary", "Dernier tour, meilleur tour, écart au meilleur et à la "
                     "référence.", NoArgs, self._laps),
            ToolSpec("get_corner_advice", "Conseils de pilotage par virage, classés par temps "
                     "gagné. `corner` optionnel pour un virage précis.", CornerArgs, self._advice),
            ToolSpec("get_gap", "Écarts en secondes avec la voiture devant et derrière en piste.",
                     NoArgs, self._gap),
            ToolSpec("get_tyres", "Pneus : tours du relais, usure restante, températures.",
                     NoArgs, self._tyres),
            ToolSpec("get_weather", "Températures de piste et d'air, évolution.", NoArgs,
                     self._weather),
            ToolSpec("set_pit_fuel", "Règle le carburant à ajouter au prochain arrêt (litres). "
                     "Uniquement sur demande explicite du pilote.", PitFuelArgs, self._set_fuel),
            ToolSpec("set_pit_tyres", "Demande ou annule le changement de pneus au prochain "
                     "arrêt. Uniquement sur demande explicite du pilote.", PitTyresArgs,
                     self._set_tyres),
        ]  # fmt: skip
        self._specs = {s.name: s for s in specs}
        # Définitions figées une fois pour toutes : préfixe de requête stable (cache de prompt).
        self.definitions: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": _schema(s.args),
                },
            }
            for s in specs
        ]

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    def execute(self, name: str, arguments: str | dict[str, Any]) -> ToolOutcome:
        """Valide puis exécute un appel d'outil. Ne lève jamais : erreur -> ok=False."""
        spec = self._specs.get(name)
        if spec is None:
            return _error(f"outil inconnu : {name}. Outils : {', '.join(self._specs)}")
        try:
            raw = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
            if not isinstance(raw, dict):
                raise ValueError("les arguments doivent être un objet JSON")
            args = spec.args.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            detail = (
                str(exc).splitlines()[0]
                if not isinstance(exc, ValidationError)
                else "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            )
            return _error(f"arguments invalides pour {name} : {detail}")
        data = spec.handler(args)
        return ToolOutcome(True, json.dumps(data, ensure_ascii=False), data)

    # --- formatage
    def _n(self, v: float, d: int = 1) -> str:
        return fmt_num(v, d, self.lang)

    def _say(self, key: str, **params: Any) -> str:
        return render(key, self.lang, **params)

    # --- lecture
    def _fuel(self, _: NoArgs) -> dict[str, Any]:
        f = self._eng.state.fuel
        out: dict[str, Any] = {
            "fuel_l": _r(f.fuel_l),
            "avg_per_lap_l": _r(f.avg_per_lap_l, 2),
            "laps_in_tank": _r(f.laps_in_tank),
            "race_laps_remaining": _r(f.laps_remaining),
            "fuel_to_add_l": math.ceil(f.fuel_to_add_l) if f.fuel_to_add_l is not None else None,
            "margin_laps": _r(f.margin_laps),
            "can_finish": f.can_finish,
        }
        plan = getattr(self._eng.state, "fuel_save", None)
        if plan is not None:
            out["fuel_saving"] = {
                "target_l_per_lap": _r(plan.target_per_lap_l, 2),
                "save_l_per_lap": _r(plan.current_per_lap_l - plan.target_per_lap_l, 2),
                "est_time_cost_s_per_lap": _r(plan.cost_per_lap_s, 2),
                "lifts": [
                    {"corner": f"T{x.corner:02d}", "label": x.label, "lift_m": round(x.lift_m)}
                    for x in plan.lifts
                ],
            }
        if plan is not None:
            out["say"] = self._say(
                "fuel_save_status",
                target=self._n(plan.target_per_lap_l, 2),
                lifts=", ".join(f"{round(x.lift_m)} m T{x.corner}" for x in plan.lifts),
            )
        elif f.laps_in_tank is None:
            out["say"] = self._say("fuel_unknown")
        elif f.laps_remaining is None:
            out["say"] = self._say("fuel_laps", laps=self._n(f.laps_in_tank))
        elif f.can_finish:
            out["say"] = self._say("fuel_ok", margin=self._n(f.margin_laps or 0.0))
        else:
            out["say"] = self._say("fuel_short", liters=out["fuel_to_add_l"])
        return out

    def _pit(self, _: NoArgs) -> dict[str, Any]:
        p = self._eng.state.pit
        liters = math.ceil(p.fuel_to_add_l)
        out: dict[str, Any] = {
            "advice": p.advice,
            "stops_needed": p.stops_needed,
            "window_is_open": p.is_open,
            "open_lap": p.open_lap,
            "close_lap": p.close_lap,
            "fuel_to_add_l": liters,
        }
        if p.advice == "unknown":
            out["say"] = self._say("fuel_unknown")
        elif p.advice == "no_stop":
            out["say"] = self._say("pit_no_stop")
        elif p.advice == "last_chance":
            out["say"] = self._say("pit_last_chance")
        elif p.advice == "pit_now_yellow":
            out["say"] = self._say("pit_now_yellow", liters=liters)
        else:
            out["say"] = self._say("pit_status", open=p.open_lap, close=p.close_lap, liters=liters)
        return out

    def _laps(self, _: NoArgs) -> dict[str, Any]:
        st = self._eng.state
        last, best = st.last_lap_time, st.best_lap_time
        out: dict[str, Any] = {
            "laps_completed": st.laps_completed,
            "last_lap_s": _r(last, 3),
            "best_lap_s": _r(best, 3),
            "delta_to_best_s": _r(last - best, 2)
            if last is not None and best is not None
            else None,
            "live_delta_to_reference_s": _r(self._coach.delta_s, 2),
        }
        if last is None:
            out["say"] = self._say("lap_none")
        elif best is None:
            out["say"] = self._say("lap_time", time=fmt_lap_time(last, self.lang))
        else:
            out["say"] = self._say(
                "lap_summary",
                time=fmt_lap_time(last, self.lang),
                best=fmt_lap_time(best, self.lang),
            )
        return out

    def _advice(self, args: CornerArgs) -> dict[str, Any]:
        analysis = self._coach.last_analysis
        if analysis is None:
            return {"advice": [], "say": self._say("dlg_no_advice")}
        items = [a for a in analysis.advice if args.corner is None or a.corner == args.corner]
        out: dict[str, Any] = {
            "lap": analysis.lap_number,
            "lap_delta_to_reference_s": _r(analysis.delta_s, 2),
            "advice": [
                {"corner": f"T{a.corner:02d}", "est_gain_s": _r(a.gain_s, 2), "text": a.long}
                for a in items[:3]
            ],
        }
        out["say"] = items[0].short if items else self._say("dlg_no_advice")
        return out

    def _gap(self, _: NoArgs) -> dict[str, Any]:
        g = self._eng.state.gaps
        race = getattr(self._eng.state, "race", None)
        ahead, behind = _r(g.ahead_s), _r(g.behind_s)
        out: dict[str, Any] = {"ahead_s": ahead, "behind_s": behind}
        if race is not None and race.position is not None:
            # Classement officiel : écarts au classement plutôt qu'en piste.
            if race.gap_ahead_s is not None or race.gap_behind_s is not None:
                ahead, behind = _r(race.gap_ahead_s), _r(race.gap_behind_s)
                out.update(ahead_s=ahead, behind_s=behind, basis="standings")
            out.update(
                position=race.position,
                positions_gained=race.gained,
                ahead_trend_s_per_lap=_r(race.ahead_rate_s, 2),
                behind_trend_s_per_lap=_r(race.behind_rate_s, 2),
                incidents=race.incidents,
                incident_limit=race.incident_limit,
            )
        if ahead is None and behind is None:
            out["say"] = self._say("gap_none")
        elif behind is None:
            out["say"] = self._say("gap_ahead_only", ahead=self._n(ahead or 0.0))
        elif ahead is None:
            out["say"] = self._say("gap_behind_only", behind=self._n(behind))
        else:
            out["say"] = self._say("gap_status", ahead=self._n(ahead), behind=self._n(behind))
        return out

    def _tyres(self, _: NoArgs) -> dict[str, Any]:
        t = self._eng.state.tyres
        out: dict[str, Any] = {
            "stint_laps": t.stint_laps,
            "wear_left_pct": _r(t.wear_pct, 0),
            "wear_per_lap_pct": _r(t.wear_per_lap_pct, 2),
            "temps_c_at_last_stop": {k: [round(x) for x in v] for k, v in t.temps_c.items()},
        }
        if t.wear_pct is None:
            out["say"] = self._say("tyres_stint", laps=t.stint_laps)
        else:
            out["say"] = self._say("tyres_status", laps=t.stint_laps, wear=round(t.wear_pct))
        return out

    def _weather(self, _: NoArgs) -> dict[str, Any]:
        w = self._eng.state.weather
        out: dict[str, Any] = {
            "track_temp_c": _r(w.track_temp_c, 0),
            "air_temp_c": _r(w.air_temp_c, 0),
            "track_temp_change_c": _r(w.track_temp_delta_c, 0),
        }
        if w.track_temp_c is None or w.air_temp_c is None:
            out["say"] = self._say("dlg_no_data")
        else:
            out["say"] = self._say(
                "weather_status", temp=round(w.track_temp_c), air=round(w.air_temp_c)
            )
        return out

    # --- écriture (commandes de stand, désactivées par défaut)
    # Rien n'est envoyé au simulateur sans « oui » du pilote (si `confirm`) : une phrase mal
    # transcrite ne peut pas changer le ravitaillement.
    def _set_fuel(self, args: PitFuelArgs) -> dict[str, Any]:
        liters = math.ceil(args.liters)
        if not self.confirm or not self._eng.pit_commands_available:
            return {"say": self._eng.set_pit_fuel(liters)}
        return self._stage("fuel", float(liters), self._say("pit_confirm_fuel", liters=liters))

    def _set_tyres(self, args: PitTyresArgs) -> dict[str, Any]:
        if not self.confirm or not self._eng.pit_commands_available:
            return {"say": self._eng.set_pit_tyres(args.change)}
        key = "pit_confirm_tyres_on" if args.change else "pit_confirm_tyres_off"
        return self._stage("tyres", float(args.change), self._say(key))

    def _stage(self, kind: str, value: float, question: str) -> dict[str, Any]:
        self._pending = PendingPitCommand(kind, value, self._clock() + self._timeout)
        return {"say": question, "awaiting_confirmation": True}

    @property
    def pending(self) -> PendingPitCommand | None:
        if self._pending is not None and self._clock() > self._pending.expires_at:
            self._pending = None  # trop tard : il faut redemander
        return self._pending

    def confirm_pending(self) -> str:
        cmd = self.pending
        self._pending = None
        if cmd is None:
            return self._say("pit_nothing")
        if cmd.kind == "fuel":
            return self._eng.set_pit_fuel(cmd.value)
        return self._eng.set_pit_tyres(bool(cmd.value))

    def cancel_pending(self) -> str:
        had = self.pending is not None
        self._pending = None
        return self._say("pit_cancelled" if had else "pit_nothing")


def _schema(model: type[_Args]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def _error(message: str) -> ToolOutcome:
    data = {"error": message}
    return ToolOutcome(False, json.dumps(data, ensure_ascii=False), data)


# --- garde-fou : tout chiffre de la réponse doit venir d'un outil
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for raw in _NUM.findall(text):
        norm = raw.replace(",", ".")
        if "." in norm:
            norm = norm.rstrip("0").rstrip(".")
        out.add(norm.lstrip("0") or "0")
    return out


def ungrounded_numbers(answer: str, sources: list[str]) -> set[str]:
    """Chiffres de `answer` absents de toutes les `sources` (sorties d'outils, question)."""
    allowed: set[str] = set()
    for s in sources:
        allowed |= numbers_in(s)
    return numbers_in(answer) - allowed


__all__ = ["ToolOutcome", "ToolRegistry", "fmt_delta", "numbers_in", "ungrounded_numbers"]
