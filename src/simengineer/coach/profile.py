"""Profil pilote : tendances par virage sur plusieurs séances, pour un circuit et une voiture."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from simengineer.coach.advice import ISSUE_THEME
from simengineer.coach.report import issue_label, theme_label
from simengineer.store.db import Store
from simengineer.voice.templates import fmt_lap_time, fmt_num

TREND_EPS_S = 0.02  # s par séance : en dessous, la tendance est « stable »
CONSISTENCY_RATIO = 0.006  # écart-type > 0,6 % du meilleur tour : régularité à travailler


@dataclass(frozen=True, slots=True)
class CornerTrend:
    corner: str
    sessions: int
    laps: int
    mean_loss_s: float  # perte moyenne par tour, toutes séances
    last_loss_s: float  # perte moyenne à la dernière séance
    trend_s_per_session: float  # pente : négatif = en progrès
    main_issue: str
    issue_share: float  # part des tours où ce défaut est le principal

    @property
    def status(self) -> str:
        if self.sessions < 2 or abs(self.trend_s_per_session) < TREND_EPS_S:
            return "stable"
        return "improving" if self.trend_s_per_session < 0 else "regressing"


@dataclass(slots=True)
class DriverProfile:
    track: str
    car: str
    sessions: int
    laps_valid: int
    best_lap_s: float | None
    consistency_s: float | None  # écart-type moyen des tours valides par séance
    corners: list[CornerTrend] = field(default_factory=list)
    theme_cost_s: dict[str, float] = field(default_factory=dict)  # s perdues par tour, par thème

    @property
    def weaknesses(self) -> list[CornerTrend]:
        # Sans défaut identifié, la perte vient d'un virage précédent (sortie compromise).
        losing = [c for c in self.corners if c.mean_loss_s > 0.02 and c.main_issue]
        return sorted(losing, key=lambda c: -c.last_loss_s)[:3]

    @property
    def strengths(self) -> list[CornerTrend]:
        return sorted(self.corners, key=lambda c: c.mean_loss_s)[:2]

    @property
    def main_theme(self) -> str | None:
        if not self.theme_cost_s:
            return None
        return max(self.theme_cost_s, key=lambda k: self.theme_cost_s[k])


def build_profile(store: Store, track: str, car: str) -> DriverProfile | None:
    sessions = store.sessions(track=track, car=car)
    per_session: dict[int, dict[int, list[float]]] = {}
    issues: dict[int, Counter[str]] = defaultdict(Counter)
    laps_by_corner: Counter[int] = Counter()
    valid_times: list[float] = []
    stds: list[float] = []
    for s in sessions:
        times = [r.lap_time for r in store.laps(s.id, valid_only=True)]
        valid_times += times
        if len(times) > 1:
            stds.append(statistics.pstdev(times))
        results = store.corner_results(s.id)
        if not results:
            continue
        by_corner: dict[int, list[float]] = defaultdict(list)
        for r in results:
            by_corner[int(r["corner"])].append(float(r["loss_s"]))
            issues[int(r["corner"])][r["issue"]] += 1
            laps_by_corner[int(r["corner"])] += 1
        per_session[s.id] = by_corner
    if not per_session:
        return None
    profile = DriverProfile(
        track=track,
        car=car,
        sessions=len(per_session),
        laps_valid=len(valid_times),
        best_lap_s=min(valid_times) if valid_times else None,
        consistency_s=statistics.fmean(stds) if stds else None,
    )
    order = sorted(per_session)
    corners = sorted({c for by in per_session.values() for c in by})
    theme_cost: dict[str, float] = defaultdict(float)
    for c in corners:
        means = [statistics.fmean(per_session[sid][c]) for sid in order if c in per_session[sid]]
        all_losses = [x for sid in order for x in per_session[sid].get(c, [])]
        slope = float(np.polyfit(np.arange(len(means)), means, 1)[0]) if len(means) > 1 else 0.0
        named = Counter({k: v for k, v in issues[c].items() if k})
        main, count = named.most_common(1)[0] if named else ("", 0)
        trend = CornerTrend(
            corner=f"T{c:02d}",
            sessions=len(means),
            laps=laps_by_corner[c],
            mean_loss_s=statistics.fmean(all_losses),
            last_loss_s=means[-1],
            trend_s_per_session=slope,
            main_issue=main,
            issue_share=count / max(laps_by_corner[c], 1),
        )
        profile.corners.append(trend)
        if main and trend.last_loss_s > 0:
            theme_cost[ISSUE_THEME[main]] += trend.last_loss_s
    best, cons = profile.best_lap_s, profile.consistency_s
    if best and cons and cons > CONSISTENCY_RATIO * best:
        theme_cost["consistency"] += cons
    profile.theme_cost_s = dict(theme_cost)
    return profile


_STATUS = {
    "fr": {"improving": "en progrès", "stable": "stable", "regressing": "en recul"},
    "en": {"improving": "improving", "stable": "stable", "regressing": "regressing"},
}


def render_profile(p: DriverProfile, lang: str = "fr") -> str:
    fr = lang == "fr"
    st = _STATUS["fr" if fr else "en"]
    best = fmt_lap_time(p.best_lap_s, lang) if p.best_lap_s else "-"
    lines = [
        f"# {'Profil pilote' if fr else 'Driver profile'} — {p.track} — {p.car}",
        "",
        f"- {'Séances analysées' if fr else 'Sessions analysed'} : {p.sessions}"
        f" ({p.laps_valid} {'tours valides' if fr else 'valid laps'})",
        f"- {'Meilleur tour' if fr else 'Best lap'} : {best}",
    ]
    if p.consistency_s is not None:
        lines.append(
            f"- {'Régularité (écart-type)' if fr else 'Consistency (std dev)'} : "
            f"{fmt_num(p.consistency_s, 2, lang)} s"
        )
    if p.main_theme:
        lines.append(
            f"- {'Thème dominant' if fr else 'Main theme'} : {theme_label(p.main_theme, lang)}"
        )
    lines += ["", f"## {'Points faibles' if fr else 'Weaknesses'}", ""]
    for c in p.weaknesses:
        lines.append(
            f"- **{c.corner}** : {fmt_num(c.last_loss_s, 2, lang)} s "
            f"{'perdues par tour' if fr else 'lost per lap'} — {issue_label(c.main_issue, lang)}"
            f" ({round(c.issue_share * 100)} %) — {st[c.status]}"
        )
    if not p.weaknesses:
        lines.append(
            "- " + ("aucun virage nettement perdant" if fr else "no clearly losing corner")
        )
    lines += ["", f"## {'Points forts' if fr else 'Strengths'}", ""]
    for c in p.strengths:
        lines.append(f"- **{c.corner}** : {fmt_num(c.mean_loss_s, 2, lang)} s")
    lines += [
        "",
        f"## {'Tendance par virage' if fr else 'Trend per corner'}",
        "",
        "| Virage | Séances | Perte moy. (s) | Dernière (s) | Tendance (s/séance) | État |"
        if fr
        else "| Corner | Sessions | Mean loss (s) | Last (s) | Trend (s/session) | Status |",
        "|---|---|---|---|---|---|",
    ]
    for c in p.corners:
        lines.append(
            f"| {c.corner} | {c.sessions} | {fmt_num(c.mean_loss_s, 2, lang)} |"
            f" {fmt_num(c.last_loss_s, 2, lang)} | {fmt_num(c.trend_s_per_session, 3, lang)} |"
            f" {st[c.status]} |"
        )
    return "\n".join(lines) + "\n"
