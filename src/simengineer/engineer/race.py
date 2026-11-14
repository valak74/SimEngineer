"""Conscience de course : incidents, voie des stands, position, écarts, derniers tours,
briefing d'avant-course, débrief à l'arrivée, et déroulé de la qualification.

Tout est calculé ici à partir de la télémétrie (règle 3). Variables supposées :
`PlayerCarMyIncidentCount`, `CarIdxPosition`, `CarIdxF2Time`, limites lues dans les infos de
session (`IncidentLimit`, `TrackPitSpeedLimit`) : à vérifier (docs/ASSUMPTIONS.md).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import Flags
from simengineer.telemetry.laps import Lap
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import fmt_delta, fmt_lap_time, fmt_num, render

INCIDENT_WARN_LEFT = 4
PIT_SPEED_TOLERANCE_KMH = 2.0
GAP_TREND_LAPS = 3
GAP_TREND_MIN_RATE_S = 0.15  # s par tour
GAP_TREND_MAX_GAP_S = 5.0
SECTORS = (1 / 3, 2 / 3)


@dataclass(frozen=True, slots=True)
class History:
    """Moyennes de tes séances précédentes sur ce circuit avec cette voiture."""

    fuel_per_lap_l: float | None
    lap_time_s: float | None
    laps: int


@dataclass(slots=True)
class RaceStatus:
    position: int | None = None
    start_position: int | None = None
    gap_ahead_s: float | None = None
    gap_behind_s: float | None = None
    ahead_rate_s: float | None = None  # + = tu reprends du temps
    behind_rate_s: float | None = None  # + = il reprend du temps
    incidents: int = 0
    incident_limit: int | None = None
    finished: bool = False
    gaps_by_lap: deque[tuple[float | None, float | None]] = field(
        default_factory=lambda: deque(maxlen=GAP_TREND_LAPS + 1)
    )

    @property
    def gained(self) -> int | None:
        if self.position is None or self.start_position is None:
            return None
        return self.start_position - self.position


class RaceMonitor:
    def __init__(
        self,
        info: SessionInfo,
        lang: str = "fr",
        history: History | None = None,
        best_lap: Callable[[], float | None] = lambda: None,
        live_delta: Callable[[], float | None] = lambda: None,
        margin_laps: float = 1.0,
        reserve_l: float = 0.3,
    ) -> None:
        self.info = info
        self.lang = lang
        self.history = history
        self._best_lap = best_lap
        self._live_delta = live_delta
        self._margin = margin_laps
        self._reserve = reserve_l
        self.status = RaceStatus(incident_limit=info.incident_limit)
        self._inc_seen: int | None = None
        self._was_on_pit = False
        self._pit_over_since: float | None = None
        self._pit_warned = -1e9
        self._briefed = False
        self._checkered = False
        self._announced_pos: int | None = None
        self._last_second = -1
        self._said: set[str] = set()
        self._time_marks_done: set[int] = set()
        # qualification
        self._q_lap_kind = "out"  # out | flying | cooldown
        self._q_sector = 0
        self._q_prev_pct = -1.0

    def _u(
        self, key: str, prio: Priority = Priority.P1, dedupe: str | None = None,
        ttl: float | None = None, **params: Any,
    ) -> Utterance:  # fmt: skip
        return Utterance(prio, dedupe or key, render(key, self.lang, **params), ttl_s=ttl)

    def _n(self, v: float, d: int = 1) -> str:
        return fmt_num(v, d, self.lang)

    # --- image par image (tests O(1))
    def on_frame(self, f: Frame) -> list[Utterance]:
        out: list[Utterance] = []
        self._incidents(f, out)
        self._pit_lane(f, out)
        if f.session_flags & Flags.CHECKERED:
            self._checkered = True
        if self.info.is_race and not self._briefed and f.speed > 5.0 and not f.on_pit_road:
            self._briefed = True
            out.append(self.briefing(f))
        if self.info.is_qualifying:
            self._quali(f, out)
        second = int(f.session_time)
        if second != self._last_second:
            self._last_second = second
            self._standings(f)
            self._time_marks(f, out)
        return out

    def _incidents(self, f: Frame, out: list[Utterance]) -> None:
        st = self.status
        if self._inc_seen is None:
            self._inc_seen = f.incident_count
            st.incidents = f.incident_count
            return
        if f.incident_count <= self._inc_seen:
            return
        self._inc_seen = st.incidents = f.incident_count
        limit = st.incident_limit
        if limit:
            out.append(self._u("incident", total=f.incident_count, limit=limit, dedupe="incident"))
            left = limit - f.incident_count
            if 0 < left <= INCIDENT_WARN_LEFT:
                out.append(self._u("incident_warning", left=left))
        else:
            out.append(self._u("incident_nolimit", total=f.incident_count, dedupe="incident"))

    def _pit_lane(self, f: Frame, out: list[Utterance]) -> None:
        limit = self.info.pit_speed_limit_kmh
        entering = f.on_pit_road and not self._was_on_pit
        self._was_on_pit = f.on_pit_road
        if not limit:
            return
        if entering:
            out.append(self._u("pit_lane_limit", kmh=round(limit), ttl=6.0))
        if not f.on_pit_road:
            self._pit_over_since = None
            return
        if f.speed * 3.6 > limit + PIT_SPEED_TOLERANCE_KMH:
            if self._pit_over_since is None:
                self._pit_over_since = f.session_time
            held = f.session_time - self._pit_over_since >= 0.3
            if held and f.session_time - self._pit_warned >= 5.0:
                self._pit_warned = f.session_time
                out.append(self._u("pit_speeding", Priority.P0, ttl=2.0))
        else:
            self._pit_over_since = None

    def _standings(self, f: Frame) -> None:
        pos_arr, f2 = f.car_idx_position, f.car_idx_f2_time
        me = f.player_car_idx
        st = self.status
        if pos_arr is None or not 0 <= me < len(pos_arr) or pos_arr[me] <= 0:
            return
        st.position = int(pos_arr[me])
        if st.start_position is None and self.info.is_race and f.speed > 5.0:
            st.start_position = st.position
        if f2 is None:
            return
        ahead = [i for i, p in enumerate(pos_arr) if p == st.position - 1]
        behind = [i for i, p in enumerate(pos_arr) if p == st.position + 1]
        st.gap_ahead_s = float(f2[me] - f2[ahead[0]]) if ahead else None
        st.gap_behind_s = float(f2[behind[0]] - f2[me]) if behind else None

    def _time_marks(self, f: Frame, out: list[Utterance]) -> None:
        if not self.info.is_race or f.session_laps_remain >= 0:
            return
        remain = f.session_time_remain
        if not math.isfinite(remain) or remain > 7 * 24 * 3600 - 1:
            return
        for minutes in (10, 5):
            mark = minutes * 60
            if remain <= mark and mark not in self._time_marks_done:
                self._time_marks_done.add(mark)
                if remain > mark - 30:
                    out.append(self._u("minutes_to_go", min=minutes, dedupe=f"min{minutes}"))

    # --- briefing
    def briefing(self, f: Frame) -> Utterance:
        info, hist = self.info, self.history
        parts: list[str] = []
        laps: float | None = None
        if info.session_laps:
            laps = float(info.session_laps)
            parts.append(render("briefing_laps", self.lang, laps=info.session_laps))
        elif info.session_time_s and hist and hist.lap_time_s:
            laps = math.ceil(info.session_time_s / hist.lap_time_s) + 1
            parts.append(
                render(
                    "briefing_time", self.lang, min=round(info.session_time_s / 60), laps=int(laps)
                )
            )
        if laps and hist and hist.fuel_per_lap_l and math.isfinite(f.fuel_level):
            need = laps * hist.fuel_per_lap_l + self._reserve
            have = f.fuel_level
            if have >= need + self._margin * hist.fuel_per_lap_l:
                verdict = "verdict_enough"
            elif have >= need * 0.92:
                verdict = "verdict_save"
            else:
                verdict = "verdict_stop"
            parts.append(
                render(
                    "briefing_fuel",
                    self.lang,
                    need=math.ceil(need),
                    have=math.floor(have),
                    verdict=render(verdict, self.lang),
                )
            )
        elif laps:
            parts.append(render("briefing_nofuel", self.lang))
        if self.status.incident_limit:
            parts.append(render("briefing_incidents", self.lang, limit=self.status.incident_limit))
        return Utterance(Priority.P1, "briefing", " ".join(parts), ttl_s=60.0)

    # --- fin de tour
    def on_lap(self, lap: Lap, f: Frame | None) -> list[Utterance]:
        out: list[Utterance] = []
        st = self.status
        if self.info.is_race:
            if self._checkered and not st.finished:
                st.finished = True
                out.append(self.debrief())
                return out
            self._position_call(out)
            self._gap_trend(out)
            if f is not None and f.session_laps_remain in (5, 2):
                out.append(
                    self._u("laps_to_go", laps=f.session_laps_remain,
                            dedupe=f"togo{f.session_laps_remain}")
                )  # fmt: skip
        if self.info.is_qualifying:
            self._quali_lap_end(lap, out)
        return out

    def _position_call(self, out: list[Utterance]) -> None:
        st = self.status
        if st.position is None:
            return
        prev, self._announced_pos = self._announced_pos, st.position
        if prev is None or prev == st.position:
            return
        n = abs(prev - st.position)
        key = "position_gain" if st.position < prev else "position_loss"
        out.append(self._u(key, pos=st.position, n=n, s="s" if n > 1 else "", dedupe="position"))

    def _gap_trend(self, out: list[Utterance]) -> None:
        st = self.status
        st.gaps_by_lap.append((st.gap_ahead_s, st.gap_behind_s))
        if len(st.gaps_by_lap) <= GAP_TREND_LAPS:
            return
        (a0, b0), (a1, b1) = st.gaps_by_lap[0], st.gaps_by_lap[-1]
        st.ahead_rate_s = (a0 - a1) / GAP_TREND_LAPS if a0 is not None and a1 is not None else None
        st.behind_rate_s = (b0 - b1) / GAP_TREND_LAPS if b0 is not None and b1 is not None else None
        if (
            st.behind_rate_s is not None and b1 is not None and b1 < GAP_TREND_MAX_GAP_S
            and st.behind_rate_s >= GAP_TREND_MIN_RATE_S
        ):  # fmt: skip
            out.append(
                self._u("gap_closing_behind", gap=self._n(b1), rate=self._n(st.behind_rate_s),
                        dedupe="gap_trend", ttl=30.0)
            )  # fmt: skip
            st.gaps_by_lap.clear()
        elif (
            st.ahead_rate_s is not None and a1 is not None and a1 < GAP_TREND_MAX_GAP_S
            and st.ahead_rate_s >= GAP_TREND_MIN_RATE_S
        ):  # fmt: skip
            out.append(
                self._u("gap_closing_ahead", gap=self._n(a1), rate=self._n(st.ahead_rate_s),
                        dedupe="gap_trend", ttl=30.0)
            )  # fmt: skip
            st.gaps_by_lap.clear()

    def debrief(self) -> Utterance:
        st = self.status
        gain = ""
        if st.gained:
            n = abs(st.gained)
            plural = "s" if n > 1 else ""
            gain = render("debrief_gain" if st.gained > 0 else "debrief_loss", self.lang, n=n,
                          s=plural)  # fmt: skip
        best = self._best_lap()
        text = render(
            "debrief", self.lang, pos=st.position or "?", gain=gain,
            best=fmt_lap_time(best, self.lang) if best else "-", inc=st.incidents,
        )  # fmt: skip
        return Utterance(Priority.P1, "debrief", text, ttl_s=120.0)

    # --- qualification
    def _quali(self, f: Frame, out: list[Utterance]) -> None:
        pct, prev = f.lap_dist_pct, self._q_prev_pct
        self._q_prev_pct = pct
        if prev < 0:
            if f.on_pit_road or pct > 0.5:
                out.append(self._u("quali_outlap", ttl=20.0))
            return
        if self._q_lap_kind != "flying":
            return
        for k, mark in enumerate(SECTORS, start=1):
            if prev < mark <= pct and self._q_sector < k:
                self._q_sector = k
                delta = self._live_delta()
                if delta is not None and abs(delta) < 0.005:
                    out.append(
                        self._u("quali_sector_even", Priority.P2, f"sector{k}", ttl=6.0, n=k)
                    )
                elif delta is not None:
                    out.append(
                        self._u("quali_sector", Priority.P2, f"sector{k}", ttl=6.0, n=k,
                                delta=fmt_delta(delta, self.lang))
                    )  # fmt: skip

    def _quali_lap_end(self, lap: Lap, out: list[Utterance]) -> None:
        self._q_sector = 0
        if self._q_lap_kind == "out" or lap.pit:
            self._q_lap_kind = "flying"
            out.append(self._u("quali_flying", ttl=5.0))
        elif self._q_lap_kind == "flying":
            self._q_lap_kind = "flying"  # on enchaîne les tours lancés tant qu'on n'est pas rentré
