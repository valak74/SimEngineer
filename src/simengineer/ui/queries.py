"""Requêtes d'historique pour l'interface.

Fonctions de premier niveau, sérialisables : exécutées dans un processus à basse priorité
(règle 4) pour ne jamais ralentir le simulateur, même si on consulte l'historique en roulant.
Elles ne renvoient que des structures JSON.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from simengineer.coach.corners import Corner, detect_corners
from simengineer.coach.pipeline import reanalyze_session
from simengineer.coach.profile import build_profile
from simengineer.coach.report import build_session_summary, issue_label, theme_label
from simengineer.coach.training import build_plan
from simengineer.core.config import CoachConfig, SetupConfig
from simengineer.reference.store import list_references
from simengineer.setup.compare import compare_mark, list_marks
from simengineer.setup.diagnosis import diagnose
from simengineer.setup.guide import apply_guide, describe, suggestions
from simengineer.store.db import Store


def _store(data_dir: str) -> Store:
    return Store(Path(data_dir))


def list_sessions(data_dir: str) -> list[dict[str, Any]]:
    store = _store(data_dir)
    try:
        out = []
        for s in reversed(store.sessions()):
            laps = store.laps(s.id)
            valid = [r.lap_time for r in laps if r.valid]
            out.append(
                {
                    "id": s.id,
                    "started_at": s.started_at,
                    "track": s.track,
                    "car": s.car,
                    "type": s.session_type,
                    "source": s.source,
                    "laps": len(laps),
                    "valid": len(valid),
                    "best_s": min(valid) if valid else None,
                }
            )
        return out
    finally:
        store.close()


def session_detail(data_dir: str, sid: int, coach: CoachConfig, lang: str) -> dict[str, Any]:
    store = _store(data_dir)
    try:
        summary = build_session_summary(store, sid)
        label, analyses = reanalyze_session(store, sid, coach, lang)
        for c in summary["top_corners"]:
            c["issue_label"] = issue_label(c["main_issue"], lang)
        summary["theme_label"] = theme_label(summary["theme"], lang) if summary["theme"] else None
        return {
            "id": sid,
            "summary": summary,
            "ref_label": label,
            "analyses": [
                {
                    "lap": a.lap_number,
                    "time_s": a.lap_time,
                    "delta_s": a.delta_s,
                    "corners": [
                        {"corner": d.corner.name, "loss_s": round(d.loss_s, 3)} for d in a.deltas
                    ],
                    "advice": [
                        {
                            "corner": f"T{x.corner:02d}",
                            "issue": x.issue,
                            "gain_s": round(x.gain_s, 3),
                            "short": x.short,
                            "text": x.long,
                        }
                        for x in a.advice
                    ],
                }
                for a in analyses
            ],
        }
    finally:
        store.close()


def driver_profile(data_dir: str, track: str, car: str, lang: str) -> dict[str, Any] | None:
    store = _store(data_dir)
    try:
        if not track or not car:
            last = store.last_session_id()
            if last is None:
                return None
            sess = store.session(last)
            track, car = track or sess.track, car or sess.car
        prof = build_profile(store, track, car)
        if prof is None:
            return {"track": track, "car": car, "empty": True}
        plan = build_plan(prof, lang, avoid=store.kv_get(f"plan_last:{track}:{car}"))
        return {
            "track": track,
            "car": car,
            "empty": False,
            "sessions": prof.sessions,
            "laps_valid": prof.laps_valid,
            "best_lap_s": prof.best_lap_s,
            "consistency_s": prof.consistency_s,
            "main_theme": theme_label(prof.main_theme, lang) if prof.main_theme else None,
            "corners": [
                {
                    **dataclasses.asdict(c),
                    "status": c.status,
                    "issue_label": issue_label(c.main_issue, lang),
                }
                for c in prof.corners
            ],
            "weaknesses": [c.corner for c in prof.weaknesses],
            "plan": dataclasses.asdict(plan),
        }
    finally:
        store.close()


def setup_view(
    data_dir: str, sid: int, setup: SetupConfig, coach: CoachConfig, lang: str
) -> dict[str, Any]:
    store = _store(data_dir)
    try:
        grids = [
            store.load_grid(r.npz_path) for r in store.laps(sid, valid_only=True) if r.npz_path
        ]
        out: dict[str, Any] = {"id": sid, "laps": len(grids), "guide": apply_guide(lang)}
        if grids:
            stored = store.kv_get(f"trackmap:{store.session(sid).track}")
            corners = (
                [Corner.from_dict(c) for c in stored] if stored else detect_corners(grids[0], coach)
            )
            temps = store.kv_get(f"tyres:{sid}") or {}
            d = diagnose(grids, corners, setup, {k: (v[0], v[1], v[2]) for k, v in temps.items()})
            out["balance"] = {k: dataclasses.asdict(v) for k, v in d.balance.items()}
            out["findings"] = [
                {"key": f.key, "title": describe(f, lang), "suggestions": suggestions(f, lang)}
                for f in d.findings
            ]
            out["tyre_temps"] = temps
        marks = list_marks(store, sid)
        out["marks"] = [dataclasses.asdict(m) for m in marks]
        cmp_ = compare_mark(store, sid, setup, coach) if marks else None
        out["comparison"] = (
            {
                "description": cmp_.description,
                "verdict": cmp_.verdict,
                "mean_delta_s": cmp_.mean_delta_s,
                "before": dataclasses.asdict(cmp_.before),
                "after": dataclasses.asdict(cmp_.after),
            }
            if cmp_
            else None
        )
        return out
    finally:
        store.close()


def references(data_dir: str) -> list[dict[str, Any]]:
    store = _store(data_dir)
    try:
        return [dataclasses.asdict(r) for r in list_references(store)]
    finally:
        store.close()
