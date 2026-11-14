"""Comparaison à une référence, règles seuillées et conseils classés par gain estimé.

Tout est chiffré en code ; le texte vient des gabarits (aucun LLM ici).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simengineer.coach.corners import Corner
from simengineer.coach.metrics import CornerMetrics, lap_metrics
from simengineer.core.config import CoachConfig
from simengineer.telemetry.resample import GridLap
from simengineer.voice.templates import fmt_num, render

MS_TO_KMH = 3.6

# Catégorie de chaque défaut (thèmes du programme d'entraînement).
ISSUE_THEME: dict[str, str] = {
    "brake_later": "braking",
    "brake_earlier": "braking",
    "brake_harder": "braking",
    "brake_faster": "braking",
    "trail_more": "trail",
    "min_speed": "min_speed",
    "throttle_earlier": "throttle",
    "exit_speed": "throttle",
    "steering": "smoothness",
}


@dataclass(frozen=True, slots=True)
class CornerDelta:
    corner: Corner
    lap: CornerMetrics
    ref: CornerMetrics

    @property
    def loss_s(self) -> float:
        """Temps perdu (+) ou gagné (-) dans la zone du virage par rapport à la référence."""
        return self.lap.time_s - self.ref.time_s


@dataclass(frozen=True, slots=True)
class Advice:
    corner: int
    issue: str
    gain_s: float  # gain estimé = temps perdu dans la zone
    params: dict[str, Any] = field(default_factory=dict)
    short: str = ""  # ≤ 8 mots, pour la voix (P2)
    long: str = ""  # détaillé, pour l'écran et les rapports

    @property
    def key(self) -> str:
        return f"adv:T{self.corner:02d}:{self.issue}"


def compare_laps(lap: GridLap, ref: GridLap, corners: list[Corner]) -> list[CornerDelta]:
    lm, rm = lap_metrics(lap, corners), lap_metrics(ref, corners)
    return [CornerDelta(c, a, b) for c, a, b in zip(corners, lm, rm, strict=True)]


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def find_issues(d: CornerDelta, cfg: CoachConfig) -> list[tuple[str, float, dict[str, Any]]]:
    """Défauts détectés sur un virage : (défaut, score = dépassement/seuil, paramètres)."""
    lap, ref = d.lap, d.ref
    out: list[tuple[str, float, dict[str, Any]]] = []
    d_bp = _diff(lap.brake_point_m, ref.brake_point_m)  # négatif = freine plus tôt
    d_peak = ref.brake_peak - lap.brake_peak
    d_vmin = (ref.min_speed - lap.min_speed) * MS_TO_KMH
    d_exit = (ref.exit_speed - lap.exit_speed) * MS_TO_KMH
    weak_brake = ref.brake_peak > 0.2 and d_peak > cfg.brake_peak
    if weak_brake:
        out.append(("brake_harder", d_peak / cfg.brake_peak, {"pct": round(d_peak * 100)}))
    if d_bp is not None and d_bp < -cfg.brake_point_m and not weak_brake:
        # Si la pression est trop faible, freiner tôt en est la conséquence, pas la cause.
        out.append(("brake_later", -d_bp / cfg.brake_point_m, {"d": round(-d_bp)}))
    if d_bp is not None and d_bp > cfg.brake_point_m and max(d_vmin, d_exit) > cfg.min_speed_kmh:
        out.append(("brake_earlier", d_bp / cfg.brake_point_m, {"d": round(d_bp)}))
    d_rise = _diff(lap.brake_rise_s, ref.brake_rise_s)
    if d_rise is not None and d_rise > cfg.brake_rise_s:
        out.append(("brake_faster", d_rise / cfg.brake_rise_s, {"ms": round(d_rise * 1000)}))
    d_trail = _diff(ref.trail_m, lap.trail_m)
    if d_trail is not None and d_trail > cfg.trail_m:
        out.append(("trail_more", d_trail / cfg.trail_m, {"d": round(d_trail)}))
    if d_vmin > cfg.min_speed_kmh and not any(i[0] == "brake_earlier" for i in out):
        out.append(("min_speed", d_vmin / cfg.min_speed_kmh, {"kmh": round(d_vmin)}))
    d_thr = _diff(lap.throttle_pickup_m, ref.throttle_pickup_m)
    if d_thr is not None and d_thr > cfg.throttle_pickup_m:
        out.append(("throttle_earlier", d_thr / cfg.throttle_pickup_m, {"d": round(d_thr)}))
    d_corr = lap.steering_corrections - ref.steering_corrections
    if d_corr >= cfg.steering_corrections:
        out.append(("steering", d_corr / cfg.steering_corrections, {"count": d_corr}))
    if not out and d_exit > cfg.exit_speed_kmh:
        # Conséquence sans cause identifiée : on signale seulement la sortie.
        out.append(("exit_speed", d_exit / cfg.exit_speed_kmh, {"kmh": round(d_exit)}))
    return out


def make_advice(
    corner: Corner, issue: str, gain_s: float, params: dict[str, Any], lang: str
) -> Advice:
    short = render(f"adv_{issue}", lang, n=corner.number, **params)
    gain = fmt_num(gain_s, 2, lang)
    long = render(f"advl_{issue}", lang, c=corner.name, gain=gain, **params)
    return Advice(corner.number, issue, gain_s, params, short, long)


def advise(deltas: list[CornerDelta], cfg: CoachConfig, lang: str = "fr") -> list[Advice]:
    """Un conseil par virage perdant (défaut principal), classés par gain estimé décroissant."""
    out: list[Advice] = []
    for d in deltas:
        if d.loss_s < cfg.min_gain_s:
            continue
        issues = find_issues(d, cfg)
        if not issues:
            continue
        issue, _score, params = max(issues, key=lambda i: i[1])
        out.append(make_advice(d.corner, issue, d.loss_s, params, lang))
    out.sort(key=lambda a: a.gain_s, reverse=True)
    return out
