"""Tendance par virage au fil de la séance : « tu perds du temps ici depuis 3 tours ».

Détecte une dégradation (pneus, fatigue, mauvaise habitude) que le débrief d'un seul tour
ne voit pas : moyenne des 3 derniers tours contre la meilleure période de 3 tours de la
séance, au même virage.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from simengineer.coach.pipeline import LapAnalysis
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import corner_word, fmt_num, render

WINDOW = 3
MIN_DROP_S = 0.15
QUIET_LAPS = 5


class CornerTrend:
    def __init__(self, lang: str = "fr", labels: dict[int, str] | None = None) -> None:
        self.lang = lang
        self.labels = dict(labels or {})
        self._losses: dict[int, list[float]] = defaultdict(list)
        self._quiet: dict[int, int] = {}  # virage -> nombre de tours de silence restant
        self._ref: tuple[str, float] | None = None

    def add(self, analysis: LapAnalysis) -> list[Utterance]:
        out: list[Utterance] = []
        ref = (analysis.ref_label, round(analysis.ref_time, 3))
        if ref != self._ref:  # nouvelle référence : pertes non comparables, on repart à zéro
            self._ref = ref
            self._losses.clear()
        for c in list(self._quiet):
            self._quiet[c] -= 1
            if self._quiet[c] <= 0:
                del self._quiet[c]
        drops: list[tuple[float, int]] = []
        for d in analysis.deltas:
            losses = self._losses[d.corner.number]
            losses.append(d.loss_s)
            if len(losses) < 2 * WINDOW or d.corner.number in self._quiet:
                continue
            recent = statistics.fmean(losses[-WINDOW:])
            earlier = losses[:-WINDOW]
            best = min(
                statistics.fmean(earlier[i : i + WINDOW]) for i in range(len(earlier) - WINDOW + 1)
            )
            # « depuis 3 tours » : chacun des 3 derniers tours doit être nettement dégradé.
            if recent - best >= MIN_DROP_S and min(losses[-WINDOW:]) - best >= MIN_DROP_S / 2:
                drops.append((recent - best, d.corner.number))
        if drops:
            drop, corner = max(drops)
            self._quiet[corner] = QUIET_LAPS
            word = corner_word(corner, self.lang, self.labels.get(corner, ""))
            out.append(
                Utterance(
                    Priority.P2, f"trend:T{corner:02d}",
                    render("trend_loss", self.lang, c=word, loss=fmt_num(drop, 1, self.lang),
                           laps=WINDOW),
                    ttl_s=45.0, cooldown_s=120.0,
                )
            )  # fmt: skip
        return out
