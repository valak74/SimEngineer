"""Mode entraînement : rappel vocal court AVANT chaque virage ciblé.

Le rappel est déclenché dans la ligne droite d'approche, `callout_lead_s` secondes avant le
point de freinage de la référence (jamais avant la sortie du virage précédent). Il est
soumis en P2 avec une durée de vie courte : si l'arbitre ne peut pas le dire à temps
(freinage, duel, autre message), il expire au lieu d'arriver en plein virage.

Le contenu vient du dernier tour analysé (conseil chiffré, ex. « freine 12 mètres plus
tard ») ; s'il n'y a plus de défaut sur ce virage : « c'était bien, pareil » ; sans
analyse : rappel générique du thème de la séance. Coût par image : une comparaison.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import numpy as np

from simengineer.coach.advice import ISSUE_THEME, Advice
from simengineer.coach.corners import Corner
from simengineer.coach.pipeline import LapAnalysis
from simengineer.core.config import CoachConfig
from simengineer.core.types import Frame
from simengineer.telemetry.resample import GridLap
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import corner_word, render

CALLOUT_TTL_S = 2.5
MIN_SPEED_MS = 15.0
ACTIVE_ISSUES = frozenset(ISSUE_THEME)


@dataclass(frozen=True, slots=True)
class CalloutPoint:
    corner: int
    trigger_m: float  # distance depuis la ligne où le rappel est déclenché
    brake_m: float  # point de freinage (ou début du virage) de la référence


def callout_points(ref: GridLap, corners: list[Corner], cfg: CoachConfig) -> list[CalloutPoint]:
    """Points de déclenchement, triés par distance, pour TOUS les virages exploitables."""
    if not corners:
        return []
    speed, brake = ref.ch("speed"), ref.ch("brake")
    step, n, length = ref.step, ref.n, ref.track_length_m
    out = []
    for i, c in enumerate(corners):
        s0 = int(c.zone_start_m / step)
        apex = min(int(c.apex_m / step), n - 1)
        on = np.flatnonzero(brake[s0 : apex + 1] > 0.1)
        bp = s0 + int(on[0]) if on.size else int(c.start_m / step)
        bp_m = bp * step
        v = float(speed[max(bp - 1, 0)])
        lead_m = cfg.callout_lead_s * v
        prev_end = corners[i - 1].end_m if i > 0 else corners[-1].end_m - length
        straight_m = bp_m - prev_end
        if straight_m < cfg.callout_min_straight_s * v:
            continue  # enchaînement : pas le temps de parler entre deux virages
        trigger = max(bp_m - lead_m, prev_end + 0.25 * straight_m)
        out.append(CalloutPoint(c.number, trigger % length, bp_m))
    return sorted(out, key=lambda p: p.trigger_m)


class CornerCallouts:
    def __init__(
        self,
        points: list[CalloutPoint],
        track_length_m: float,
        focus: list[int],
        theme: str,
        lang: str = "fr",
        labels: dict[int, str] | None = None,
    ) -> None:
        self.labels = dict(labels or {})
        self.length = track_length_m
        self.lang = lang
        self.theme = theme
        self.focus = list(focus)
        self._all = points
        self._messages: dict[int, tuple[str, str]] = {}  # virage -> (clé, texte)
        self._set_points()
        self._prev_m: float | None = None
        for c in self.focus:
            self._messages[c] = self._generic(c)

    def _set_points(self) -> None:
        focus = set(self.focus)
        self._points = sorted(
            (p for p in self._all if p.corner in focus), key=lambda p: p.trigger_m
        )
        self._triggers = [p.trigger_m for p in self._points]

    def _generic(self, corner: int) -> tuple[str, str]:
        return f"call_{self.theme}", render(f"call_{self.theme}", self.lang, c=self._word(corner))

    def _word(self, corner: int) -> str:
        return corner_word(corner, self.lang, self.labels.get(corner, ""))

    def set_points(self, points: list[CalloutPoint]) -> None:
        """Nouveaux points de déclenchement (la référence a changé)."""
        self._all = points
        self._set_points()

    def set_focus(self, corners: list[int], theme: str | None = None) -> None:
        self.focus = list(corners)
        if theme:
            self.theme = theme
        self._set_points()
        for c in self.focus:
            self._messages.setdefault(c, self._generic(c))

    @property
    def active_corners(self) -> list[int]:
        """Virages ciblés qui ont effectivement un point de rappel."""
        return [p.corner for p in self._points]

    def update_from_analysis(self, analysis: LapAnalysis) -> None:
        """Contenu des rappels d'après le dernier tour analysé."""
        by_corner: dict[int, Advice] = {}
        for a in analysis.advice:
            by_corner.setdefault(a.corner, a)
        analysed = {d.corner.number for d in analysis.deltas}
        for c in self.focus:
            adv = by_corner.get(c)
            if adv is not None and adv.issue in ACTIVE_ISSUES:
                self._messages[c] = (f"adv_{adv.issue}", adv.short)
            elif c in analysed:
                self._messages[c] = ("call_good", render("call_good", self.lang, c=self._word(c)))

    def message(self, corner: int) -> str:
        return self._messages.get(corner, self._generic(corner))[1]

    def on_frame(self, f: Frame) -> list[Utterance]:
        if not self._points:
            return []
        pos = f.lap_dist_pct * self.length
        prev, self._prev_m = self._prev_m, pos
        if prev is None or f.on_pit_road or f.speed < MIN_SPEED_MS:
            return []
        if pos < prev:  # passage de la ligne (ou retour en arrière)
            if prev - pos < 0.5 * self.length:
                return []  # retour en arrière (reset) : rien
            crossed = self._between(prev, self.length) + self._between(-1.0, pos)
        else:
            crossed = self._between(prev, pos)
        return [
            Utterance(
                Priority.P2,
                f"callout:T{p.corner:02d}",
                self.message(p.corner),
                ttl_s=CALLOUT_TTL_S,
                cooldown_s=10.0,
            )
            for p in crossed
        ]

    def _between(self, a: float, b: float) -> list[CalloutPoint]:
        """Points avec a < trigger <= b."""
        i, j = bisect_right(self._triggers, a), bisect_right(self._triggers, b)
        return self._points[i:j]


def training_focus(
    profile_focus: list[str], weaknesses: list[str], analysis: LapAnalysis | None, max_n: int
) -> list[int]:
    """Virages ciblés : ceux du programme, sinon points faibles, sinon pires du dernier tour."""
    names = profile_focus or weaknesses
    if names:
        return [int(n[1:]) for n in names][:max_n]
    if analysis is not None:
        return [a.corner for a in analysis.advice][:max_n]
    return []
