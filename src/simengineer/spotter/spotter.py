"""Spotter : état (hystérésis, cooldowns) autour des règles pures, + journal JSONL.

Entièrement local et synchrone : appelé dans la boucle 60 Hz, sans réseau ni LLM.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import IO, Any

import numpy as np

from simengineer.core.config import SpotterConfig
from simengineer.core.types import FloatArray, Frame
from simengineer.core.vars import CarLeftRight, TrackSurface
from simengineer.spotter import rules
from simengineer.voice.arbiter import Priority, Utterance
from simengineer.voice.templates import render

_WINDOW = 15  # images (~0,25 s) pour estimer la vitesse des autres voitures


class Spotter:
    def __init__(
        self,
        cfg: SpotterConfig,
        track_length_m: float,
        lang: str = "fr",
        log_path: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.length = track_length_m
        self.lang = lang
        self._log: IO[str] | None = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = log_path.open("a", encoding="utf-8")
        # côtés
        self._cand = int(CarLeftRight.OFF)
        self._cand_n = 0
        self._side = int(CarLeftRight.CLEAR)  # dernier état annoncé
        self._side_t = 0.0
        self._clear_since: float | None = None
        # voitures lentes
        self._hist: deque[tuple[float, FloatArray]] = deque(maxlen=_WINDOW + 1)
        self._slow_since: dict[int, float] = {}
        self._stage: dict[int, int] = {}  # 0 rien, 1 annoncée de loin, 2 annoncée de près
        self._stage_t: dict[int, float] = {}
        self._accident_t = -1e9
        self._near_t = -1e9
        # drapeaux / hors-piste
        self._flags: int | None = None
        self._surface = int(TrackSurface.ON_TRACK)
        self._offtrack_t = -1e9
        # contexte pour l'arbitre
        self.in_battle = False
        self.alert_count = 0

    def close(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None

    # --- sortie
    def _say(
        self,
        out: list[Utterance],
        f: Frame,
        stamp: float | None,
        key: str,
        priority: Priority,
        dedupe: str | None = None,
        cooldown_s: float = 1.0,
        **params: Any,
    ) -> None:
        text = render(key, self.lang, **params)
        out.append(Utterance(priority, dedupe or key, text, cooldown_s=cooldown_s, data_time=stamp))
        self.alert_count += 1
        if self._log is not None:
            record = {
                "t": round(f.session_time, 3),
                "lap": f.lap,
                "pct": round(f.lap_dist_pct, 4),
                "kind": key,
                "priority": int(priority),
                "text": text,
                **params,
            }
            if stamp is not None:
                record["processing_ms"] = round((time.perf_counter() - stamp) * 1000.0, 3)
            self._log.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._log.flush()

    # --- boucle
    def on_frame(self, f: Frame, stamp: float | None = None) -> list[Utterance]:
        """Traite une image. `stamp` = perf_counter de réception (mesure de latence)."""
        out: list[Utterance] = []
        self._flags_step(f, stamp, out)
        in_pits = f.on_pit_road or not f.is_on_track
        self._offtrack_step(f, stamp, out, in_pits)
        self._side_step(f, stamp, out, in_pits)
        near = self._traffic_step(f, stamp, out, in_pits)
        self.in_battle = rules.is_side_occupied(self._side) or near
        return out

    def _flags_step(self, f: Frame, stamp: float | None, out: list[Utterance]) -> None:
        if self._flags is None:
            self._flags = f.session_flags
            return
        if f.session_flags != self._flags:
            for key, prio in rules.flag_calls(self._flags, f.session_flags):
                self._say(out, f, stamp, key, prio, cooldown_s=5.0)
            self._flags = f.session_flags

    def _offtrack_step(
        self, f: Frame, stamp: float | None, out: list[Utterance], in_pits: bool
    ) -> None:
        surface = f.player_track_surface
        went_off = surface == TrackSurface.OFF_TRACK and self._surface == TrackSurface.ON_TRACK
        self._surface = surface
        cooled = f.session_time - self._offtrack_t >= self.cfg.offtrack_cooldown_s
        if went_off and not in_pits and f.speed > 10.0 and cooled:
            self._offtrack_t = f.session_time
            self._say(out, f, stamp, "offtrack_self", Priority.P0)

    def _side_step(
        self, f: Frame, stamp: float | None, out: list[Utterance], in_pits: bool
    ) -> None:
        state = f.car_left_right
        t = f.session_time
        if in_pits or state == CarLeftRight.OFF:
            self._cand, self._cand_n = int(CarLeftRight.OFF), 0
            self._side, self._clear_since = int(CarLeftRight.CLEAR), None
            return
        if state == self._cand:
            self._cand_n += 1
        else:
            self._cand, self._cand_n = state, 1
        occupied = rules.is_side_occupied(self._side)
        if state == CarLeftRight.CLEAR:
            if not occupied:
                return
            if self._clear_since is None:
                self._clear_since = t
            if t - self._clear_since >= self.cfg.clear_confirm_s:
                self._side, self._clear_since = int(CarLeftRight.CLEAR), None
                self._say(out, f, stamp, "clear", Priority.P0, cooldown_s=0.5)
            return
        self._clear_since = None
        if self._cand_n < self.cfg.side_confirm_frames:
            return  # hystérésis : l'état doit être stable quelques images
        key = rules.side_key(state)
        if key is None:
            return
        if state != self._side:
            self._side, self._side_t = state, t
            self._say(out, f, stamp, key, Priority.P0, cooldown_s=0.5)
        elif t - self._side_t >= self.cfg.side_repeat_s:
            self._side_t = t
            self._say(out, f, stamp, rules.still_key(state) or key, Priority.P0, cooldown_s=0.5)

    def _traffic_step(
        self, f: Frame, stamp: float | None, out: list[Utterance], in_pits: bool
    ) -> bool:
        """Voitures lentes devant. Renvoie True si une voiture est très proche (duel)."""
        pcts, surfaces = f.car_idx_lap_dist_pct, f.car_idx_track_surface
        if pcts is None or surfaces is None:
            return False
        t = f.session_time
        self._hist.append((t, pcts))
        t_old, p_old = self._hist[0]
        on_track = surfaces == TrackSurface.ON_TRACK
        rel = (pcts - f.lap_dist_pct + 0.5) % 1.0 - 0.5
        close = on_track & (pcts >= 0.0) & (np.abs(rel) * self.length < 25.0)
        if 0 <= f.player_car_idx < len(close):
            close[f.player_car_idx] = False
        near = bool(close.any())
        if len(self._hist) <= _WINDOW or in_pits:
            return near
        cfg = self.cfg
        speeds = rules.estimate_speeds(p_old, pcts, t - t_old, self.length)
        reach = max(cfg.stopped_alert_distance_m, cfg.accident_distance_m)
        hazards = rules.slow_cars_ahead(
            f.player_car_idx, f.lap_dist_pct, pcts, speeds, surfaces, f.car_idx_on_pit_road,
            self.length, reach, cfg.stopped_speed_ms,
        )  # fmt: skip
        seen = {h.car_idx for h in hazards}
        for idx in list(self._slow_since):
            if idx not in seen:  # repartie ou dépassée : on oublie
                del self._slow_since[idx]
                self._stage.pop(idx, None)
        confirmed = []
        for h in hazards:
            since = self._slow_since.setdefault(h.car_idx, t)
            if t - since >= cfg.stopped_confirm_s:
                confirmed.append(h)
        if not confirmed or f.speed < cfg.stopped_min_player_speed_ms:
            return near
        nearest = confirmed[0]
        dist = rules.announce_distance(nearest.distance_m)
        fresh = [h for h in confirmed if self._stage.get(h.car_idx, 0) == 0]
        if (
            len(confirmed) >= cfg.accident_min_cars
            and fresh
            and nearest.distance_m <= cfg.accident_distance_m
            and t - self._accident_t >= cfg.accident_cooldown_s
        ):
            self._accident_t = t
            for h in confirmed:
                self._stage[h.car_idx] = max(self._stage.get(h.car_idx, 0), 1)
                self._stage_t[h.car_idx] = t
            self._say(out, f, stamp, "accident_ahead", Priority.P0, "accident", dist=dist)
            return near
        stage = self._stage.get(nearest.car_idx, 0)
        key = "offtrack_ahead" if nearest.offtrack else "stopped_ahead"
        if stage == 0 and nearest.distance_m <= cfg.stopped_alert_distance_m:
            self._stage[nearest.car_idx] = 2 if nearest.distance_m <= 200.0 else 1
            self._stage_t[nearest.car_idx] = t
            self._say(
                out, f, stamp, key, Priority.P0, f"stopped:{nearest.car_idx}:far",
                dist=dist, car_idx=nearest.car_idx,
            )  # fmt: skip
        elif (
            stage == 1
            and nearest.distance_m <= cfg.stopped_repeat_distance_m
            and t - self._stage_t.get(nearest.car_idx, t) >= 1.5
            and t - self._near_t >= cfg.stopped_cooldown_s
        ):
            self._near_t = t
            self._stage[nearest.car_idx] = 2
            self._say(
                out, f, stamp, key, Priority.P0, f"stopped:{nearest.car_idx}:near",
                dist=dist, car_idx=nearest.car_idx,
            )  # fmt: skip
        return near
