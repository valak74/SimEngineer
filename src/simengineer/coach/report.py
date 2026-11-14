"""Rapport de session : résumé structuré (calculé en code) puis rendu par gabarit.

Le résumé est aussi la seule chose transmise au LLM pour le commentaire optionnel :
aucun nom de pilote, aucune donnée brute.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from simengineer.coach.advice import ISSUE_THEME
from simengineer.store.db import Store
from simengineer.voice.templates import fmt_lap_time, fmt_num

ISSUE_LABELS: dict[str, tuple[str, str]] = {
    "brake_later": ("freinage trop tôt", "braking too early"),
    "brake_earlier": ("freinage trop tard", "braking too late"),
    "brake_harder": ("pression de frein trop faible", "brake pressure too low"),
    "brake_faster": ("montée en pression trop lente", "slow brake pressure build-up"),
    "trail_more": ("relâchement du frein trop brusque", "abrupt brake release"),
    "min_speed": ("vitesse mini trop basse", "minimum speed too low"),
    "throttle_earlier": ("reprise des gaz tardive", "late throttle pickup"),
    "exit_speed": ("vitesse de sortie trop basse", "low exit speed"),
    "steering": ("trop de corrections au volant", "too many steering corrections"),
    "": ("aucun défaut net", "no clear issue"),
}
THEME_LABELS: dict[str, tuple[str, str]] = {
    "braking": ("Freinage", "Braking"),
    "trail": ("Trail-braking", "Trail braking"),
    "min_speed": ("Vitesse au point de corde", "Apex speed"),
    "throttle": ("Reprise des gaz et sortie", "Throttle pickup and exit"),
    "smoothness": ("Fluidité au volant", "Steering smoothness"),
    "consistency": ("Régularité", "Consistency"),
}


def issue_label(issue: str, lang: str = "fr") -> str:
    return ISSUE_LABELS.get(issue, (issue, issue))[0 if lang == "fr" else 1]


def theme_label(theme: str, lang: str = "fr") -> str:
    return THEME_LABELS.get(theme, (theme, theme))[0 if lang == "fr" else 1]


def corner_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrège les résultats par virage : perte moyenne, meilleure, défaut le plus fréquent."""
    by: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by[int(r["corner"])].append(r)
    out = []
    for corner, rows in sorted(by.items()):
        losses = [float(r["loss_s"]) for r in rows]
        issues = Counter(r["issue"] for r in rows if r["issue"])
        main, count = issues.most_common(1)[0] if issues else ("", 0)
        out.append(
            {
                "corner": f"T{corner:02d}",
                "laps": len(rows),
                "mean_loss_s": round(statistics.fmean(losses), 3),
                "best_loss_s": round(min(losses), 3),
                "main_issue": main,
                "issue_count": count,
                "best_time_s": round(min(float(r["metrics"]["time_s"]) for r in rows), 3),
            }
        )
    return out


def session_theme(corners: list[dict[str, Any]]) -> str | None:
    """Thème dominant : catégorie de défaut qui coûte le plus de temps au total."""
    cost: Counter[str] = Counter()
    for c in corners:
        if c["main_issue"] and c["mean_loss_s"] > 0:
            cost[ISSUE_THEME[c["main_issue"]]] += c["mean_loss_s"]
    return cost.most_common(1)[0][0] if cost else None


def build_session_summary(store: Store, session_id: int) -> dict[str, Any]:
    sess = store.session(session_id)
    laps = store.laps(session_id)
    valid = [r.lap_time for r in laps if r.valid]
    fuel = [r.fuel_used for r in laps if r.valid and r.fuel_used and r.fuel_used > 0]
    corners = corner_summaries(store.corner_results(session_id))
    losing = sorted((c for c in corners if c["mean_loss_s"] > 0), key=lambda c: -c["mean_loss_s"])
    ref = store.kv_get(f"session_ref:{session_id}")
    summary: dict[str, Any] = {
        "track": sess.track,
        "car": sess.car,
        "session_type": sess.session_type,
        "date": sess.started_at[:10],
        "laps_total": len(laps),
        "laps_valid": len(valid),
        "best_lap_s": round(min(valid), 3) if valid else None,
        "avg_valid_lap_s": round(statistics.fmean(valid), 3) if valid else None,
        "consistency_s": round(statistics.pstdev(valid), 3) if len(valid) > 1 else None,
        "theoretical_best_s": (
            round(sum(c["best_time_s"] for c in corners), 3) if corners else None
        ),
        "fuel_avg_l_per_lap": round(statistics.fmean(fuel), 2) if fuel else None,
        "reference": ref,
        "top_corners": losing[:5],
        "theme": session_theme(corners),
        "laps": [
            {
                "lap": r.number,
                "time_s": round(r.lap_time, 3),
                "valid": r.valid,
                "flags": [k for k, v in r.flags.items() if v and k != "complete"]
                + ([] if r.flags.get("complete", True) else ["incomplete"]),
            }
            for r in laps
        ],
    }
    return summary


_L = {
    "fr": {
        "title": "Rapport de session", "laps": "Tours", "valid": "valides", "best": "Meilleur tour",
        "avg": "Moyenne des tours valides", "cons": "Régularité (écart-type)",
        "theo": "Meilleur tour théorique", "fuel": "Consommation moyenne", "ref": "Référence",
        "top": "Virages où tu perds le plus", "theme": "Thème à travailler",
        "table": "Détail des tours",
        "lap": "Tour", "time": "Temps", "state": "État", "ok": "valide", "none": "Aucune donnée.",
        "loss": "perte moyenne", "per_lap": "L/tour", "times": "fois", "noref": "aucune",
    },
    "en": {
        "title": "Session report", "laps": "Laps", "valid": "valid", "best": "Best lap",
        "avg": "Average valid lap", "cons": "Consistency (std dev)",
        "theo": "Theoretical best lap", "fuel": "Average consumption", "ref": "Reference",
        "top": "Corners costing you the most", "theme": "Theme to work on", "table": "Lap details",
        "lap": "Lap", "time": "Time", "state": "Status", "ok": "valid", "none": "No data.",
        "loss": "mean loss", "per_lap": "L/lap", "times": "times", "noref": "none",
    },
}  # fmt: skip


def render_report(summary: dict[str, Any], lang: str = "fr", narrative: str = "") -> str:
    t = _L["fr" if lang == "fr" else "en"]

    def lt(v: float | None) -> str:
        return fmt_lap_time(v, lang) if v is not None else "-"

    lines = [
        f"# {t['title']} — {summary['track']} — {summary['car']}",
        "",
        f"{summary['date']} · {summary['session_type']}",
        "",
        f"- {t['laps']} : {summary['laps_total']} ({summary['laps_valid']} {t['valid']})",
        f"- {t['best']} : {lt(summary['best_lap_s'])}",
        f"- {t['avg']} : {lt(summary['avg_valid_lap_s'])}",
    ]
    if summary["consistency_s"] is not None:
        lines.append(f"- {t['cons']} : {fmt_num(summary['consistency_s'], 2, lang)} s")
    if summary["theoretical_best_s"] is not None:
        lines.append(f"- {t['theo']} : {lt(summary['theoretical_best_s'])}")
    if summary["fuel_avg_l_per_lap"] is not None:
        fuel = fmt_num(summary["fuel_avg_l_per_lap"], 2, lang)
        lines.append(f"- {t['fuel']} : {fuel} {t['per_lap']}")
    ref = summary.get("reference")
    ref_text = f"{ref['label']} ({lt(ref['lap_s'])})" if ref else t["noref"]
    lines.append(f"- {t['ref']} : {ref_text}")
    if summary["theme"]:
        lines += ["", f"## {t['theme']}", "", f"**{theme_label(summary['theme'], lang)}**"]
    if summary["top_corners"]:
        lines += ["", f"## {t['top']}", ""]
        for c in summary["top_corners"]:
            loss = fmt_num(c["mean_loss_s"], 2, lang)
            issue = issue_label(c["main_issue"], lang)
            lines.append(
                f"- **{c['corner']}** : {t['loss']} {loss} s — {issue}"
                + (f" ({c['issue_count']} {t['times']})" if c["issue_count"] else "")
            )
    if narrative:
        lines += ["", "## Commentaire" if lang == "fr" else "## Comments", "", narrative]
    lines += [
        "",
        f"## {t['table']}",
        "",
        f"| {t['lap']} | {t['time']} | {t['state']} |",
        "|---|---|---|",
    ]
    for lap in summary["laps"]:
        state = t["ok"] if lap["valid"] else ", ".join(lap["flags"])
        lines.append(f"| {lap['lap']} | {lt(lap['time_s'])} | {state} |")
    if not summary["laps"]:
        lines.append(t["none"])
    return "\n".join(lines) + "\n"
