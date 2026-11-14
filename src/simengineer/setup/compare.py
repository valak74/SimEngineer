"""Suivi avant/après d'un changement de setup, sur tours propres uniquement."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

from simengineer.coach.corners import Corner, detect_corners
from simengineer.core.config import CoachConfig, SetupConfig
from simengineer.setup.diagnosis import PhaseBalance, average_balance, lap_balance
from simengineer.store.db import Store
from simengineer.telemetry.resample import GridLap
from simengineer.voice.templates import fmt_lap_time, fmt_num

MIN_LAPS = 3


@dataclass(frozen=True, slots=True)
class SetupMark:
    id: int
    session_id: int
    created_at: str
    after_lap: int
    description: str


@dataclass(frozen=True, slots=True)
class StintStats:
    laps: int
    best_s: float | None
    mean_s: float | None
    std_s: float | None
    balance: dict[str, PhaseBalance]


@dataclass(frozen=True, slots=True)
class SetupComparison:
    description: str
    before: StintStats
    after: StintStats
    verdict: str  # better | worse | inconclusive | not_enough_laps
    mean_delta_s: float | None  # après - avant (négatif = plus rapide)


def add_mark(store: Store, session_id: int, description: str) -> SetupMark:
    """Marque « réglage changé maintenant » : s'applique après le dernier tour enregistré."""
    laps = store.laps(session_id)
    after_lap = max((r.number for r in laps), default=0)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    cur = store.db.execute(
        "INSERT INTO setup_marks (session_id, created_at, after_lap, description) VALUES (?,?,?,?)",
        (session_id, now, after_lap, description.strip()),
    )
    store.db.commit()
    return SetupMark(int(cur.lastrowid or 0), session_id, now, after_lap, description.strip())


def list_marks(store: Store, session_id: int) -> list[SetupMark]:
    rows = store.db.execute(
        "SELECT * FROM setup_marks WHERE session_id=? ORDER BY after_lap, id", (session_id,)
    )
    return [SetupMark(**dict(r)) for r in rows]


def stint_stats(grids: list[GridLap], corners: list[Corner], cfg: SetupConfig) -> StintStats:
    times = [g.lap_time for g in grids]
    return StintStats(
        laps=len(times),
        best_s=min(times) if times else None,
        mean_s=statistics.fmean(times) if times else None,
        std_s=statistics.pstdev(times) if len(times) > 1 else None,
        balance=average_balance([lap_balance(g, corners, cfg) for g in grids]),
    )


def compare_stints(
    before: list[GridLap],
    after: list[GridLap],
    corners: list[Corner],
    cfg: SetupConfig,
    description: str = "",
) -> SetupComparison:
    b, a = stint_stats(before, corners, cfg), stint_stats(after, corners, cfg)
    if b.laps < MIN_LAPS or a.laps < MIN_LAPS or b.mean_s is None or a.mean_s is None:
        return SetupComparison(description, b, a, "not_enough_laps", None)
    delta = a.mean_s - b.mean_s
    # Bruit : erreur standard combinée des deux moyennes (au moins 0,05 s).
    noise = math.sqrt((b.std_s or 0.0) ** 2 / b.laps + (a.std_s or 0.0) ** 2 / a.laps)
    margin = max(2.0 * noise, 0.05)
    verdict = "better" if delta < -margin else "worse" if delta > margin else "inconclusive"
    return SetupComparison(description, b, a, verdict, delta)


def compare_mark(
    store: Store,
    session_id: int,
    setup_cfg: SetupConfig,
    coach_cfg: CoachConfig,
    mark: SetupMark | None = None,
) -> SetupComparison | None:
    """Compare les tours valides avant et après une marque (la dernière par défaut)."""
    marks = list_marks(store, session_id)
    if not marks:
        return None
    mark = mark or marks[-1]
    earlier = [m.after_lap for m in marks if m.after_lap < mark.after_lap]
    later = [m.after_lap for m in marks if m.after_lap > mark.after_lap]
    lo, hi = (max(earlier) if earlier else -1), (min(later) if later else 10**9)
    rows = [r for r in store.laps(session_id, valid_only=True) if r.npz_path]
    before = [store.load_grid(r.npz_path or "") for r in rows if lo < r.number <= mark.after_lap]
    after = [store.load_grid(r.npz_path or "") for r in rows if mark.after_lap < r.number <= hi]
    if not before and not after:
        return compare_stints([], [], [], setup_cfg, mark.description)
    stored = store.kv_get(f"trackmap:{store.session(session_id).track}")
    corners = (
        [Corner.from_dict(c) for c in stored]
        if stored
        else detect_corners((before or after)[0], coach_cfg)
    )
    return compare_stints(before, after, corners, setup_cfg, mark.description)


_VERDICT = {
    "fr": {"better": "MIEUX — garde ce réglage", "worse": "MOINS BIEN — reviens en arrière",
           "inconclusive": "NON CONCLUANT — écart dans le bruit, roule plus de tours",
           "not_enough_laps": "PAS ASSEZ DE TOURS PROPRES (3 avant et 3 après au minimum)"},
    "en": {"better": "BETTER — keep this change", "worse": "WORSE — revert",
           "inconclusive": "INCONCLUSIVE — difference within noise, drive more laps",
           "not_enough_laps": "NOT ENOUGH CLEAN LAPS (at least 3 before and 3 after)"},
}  # fmt: skip


def render_comparison(c: SetupComparison, lang: str = "fr") -> str:
    fr = lang == "fr"
    lg = "fr" if fr else "en"

    def lt(v: float | None) -> str:
        return fmt_lap_time(v, lang) if v is not None else "-"

    def num(v: float | None, d: int = 2) -> str:
        return fmt_num(v, d, lang) if v is not None else "-"

    lines = [
        f"# {'Avant / après' if fr else 'Before / after'} : {c.description}",
        "",
        f"**Verdict : {_VERDICT[lg][c.verdict]}**",
        "",
        f"| | {'Avant' if fr else 'Before'} | {'Après' if fr else 'After'} |",
        "|---|---|---|",
        f"| {'Tours propres' if fr else 'Clean laps'} | {c.before.laps} | {c.after.laps} |",
        f"| {'Meilleur' if fr else 'Best'} | {lt(c.before.best_s)} | {lt(c.after.best_s)} |",
        f"| {'Moyenne' if fr else 'Mean'} | {lt(c.before.mean_s)} | {lt(c.after.mean_s)} |",
        f"| {'Écart-type (s)' if fr else 'Std dev (s)'} | {num(c.before.std_s)} |"
        f" {num(c.after.std_s)} |",
    ]
    names = {"entry": "entrée" if fr else "entry", "mid": "milieu" if fr else "mid",
             "exit": "sortie" if fr else "exit"}  # fmt: skip
    for phase in ("entry", "mid", "exit"):
        b, a = c.before.balance.get(phase), c.after.balance.get(phase)
        if b is None or a is None:
            continue
        label = "Indice de sous-virage" if fr else "Understeer index"
        lines.append(
            f"| {label} ({names[phase]}) | {num(b.understeer_index)} | {num(a.understeer_index)} |"
        )
        label = "Contre-braquages/tour" if fr else "Counter-steers/lap"
        lines.append(
            f"| {label} ({names[phase]}) | {num(b.oversteer_events, 1)} |"
            f" {num(a.oversteer_events, 1)} |"
        )
    if c.mean_delta_s is not None:
        word = "Écart moyen" if fr else "Mean difference"
        lines += ["", f"{word} : {fmt_num(c.mean_delta_s, 2, lang)} s"]
    return "\n".join(lines) + "\n"
