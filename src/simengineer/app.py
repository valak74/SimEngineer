"""Boucle principale : relie source, spotter, ingénieur, coach et arbitre de parole.

Un seul thread lit la télémétrie. Par image : spotter (règles pures), segmentation,
ingénieur (1 Hz), delta de référence. Les analyses de tour partent dans un worker à
basse priorité ; le dialogue vocal vit dans son propre thread.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from simengineer.coach.corners import Corner
from simengineer.coach.pipeline import (
    LapAnalysis,
    LapResult,
    corner_rows,
    debrief_utterances,
    process_lap,
)
from simengineer.coach.worker import InlineWorker, ProcessWorker, Worker
from simengineer.core.config import AppConfig
from simengineer.core.events import EventBus, LapCompleted, SessionEnded, SessionStarted
from simengineer.core.log import get_logger
from simengineer.core.types import Frame, SessionInfo
from simengineer.engineer.engineer import RaceEngineer
from simengineer.engineer.pit_commands import make_pit_commander
from simengineer.sources.base import TelemetrySource
from simengineer.spotter.spotter import Spotter
from simengineer.store.db import Store
from simengineer.telemetry.laps import Lap, LapSegmenter
from simengineer.telemetry.resample import GridLap
from simengineer.voice.arbiter import Speaker, SpeechArbiter, SpeechContext, Utterance
from simengineer.voice.speaker import NullSpeaker

log = get_logger("app")

_POLL_EVERY = 6  # images : résultats d'analyse et dialogue relevés à 10 Hz


@dataclass(slots=True)
class CoachState:
    """État du coach partagé avec les outils de dialogue (lecture seule côté LLM)."""

    corners: list[Corner] = field(default_factory=list)
    reference: GridLap | None = None
    reference_label: str = ""
    reference_is_imported: bool = False
    last_analysis: LapAnalysis | None = None
    delta_s: float | None = None  # delta temps réel par rapport à la référence


def trackmap_key(track: str) -> str:
    return f"trackmap:{track}"


class SimEngineerApp:
    def __init__(
        self,
        cfg: AppConfig,
        source: TelemetrySource,
        speaker: Speaker | None = None,
        worker: Worker | None = None,
        store: Store | None = None,
    ) -> None:
        self.cfg = cfg
        self.source = source
        self.speaker: Speaker = speaker or NullSpeaker()
        realtime = cfg.source.realtime or cfg.source.kind == "live"
        self.worker: Worker = worker or (ProcessWorker() if realtime else InlineWorker())
        self.store = store or Store(cfg.data_dir)
        self.bus = EventBus()
        self._session_clock = 0.0
        # En rejeu accéléré, l'arbitre vit au temps de la session (cooldowns, expirations).
        self.arbiter = (
            SpeechArbiter(self.speaker)
            if realtime
            else SpeechArbiter(self.speaker, lambda: self._session_clock)
        )
        self.coach = CoachState()
        self.session_id = 0
        self.info = SessionInfo()
        self.engineer: RaceEngineer | None = None
        self.spotter: Spotter | None = None
        self.frames = 0
        self.max_frame_ms = 0.0
        self.total_frame_s = 0.0
        self._frame_hooks: list[Callable[[Frame], None]] = []
        self._poll_hooks: list[Callable[[], None]] = []
        self._ready_hooks: list[Callable[[], None]] = []
        self._lap_ids: dict[int, int] = {}

    # --- extensions (delta de référence, dialogue)
    def add_frame_hook(self, hook: Callable[[Frame], None]) -> None:
        self._frame_hooks.append(hook)

    def add_poll_hook(self, hook: Callable[[], None]) -> None:
        self._poll_hooks.append(hook)

    def on_ready(self, hook: Callable[[], None]) -> None:
        """Appelé une fois la session ouverte (ingénieur et coach disponibles)."""
        self._ready_hooks.append(hook)

    def say(self, utt: Utterance) -> bool:
        return self.arbiter.submit(utt)

    # --- préparation
    def _setup(self) -> None:
        cfg = self.cfg
        self.source.open()
        self.info = self.source.session_info()
        self.session_id = self.store.create_session(self.info, self.source.name)
        commander = make_pit_commander(cfg.engineer.pit_commands_enabled, self.source)
        self.engineer = RaceEngineer(cfg.engineer, self.info, cfg.language, commander)
        if cfg.spotter.enabled:
            self.spotter = Spotter(
                cfg.spotter,
                self.info.track_length_m,
                cfg.language,
                cfg.data_dir / cfg.spotter.alerts_log,
            )
        stored = self.store.kv_get(trackmap_key(self.info.track_name))
        if stored:
            self.coach.corners = [Corner.from_dict(c) for c in stored]
        self._select_reference()
        self.bus.publish(SessionStarted(self.info, self.session_id))
        for hook in self._ready_hooks:
            hook()
        log.info(
            "Session %d : %s, %s, %s", self.session_id, self.info.track_name,
            self.info.car_name, self.info.session_type,
        )  # fmt: skip

    def _select_reference(self) -> None:
        """Référence importée pour ce circuit et cette voiture, si le pilote en possède une."""
        from simengineer.reference.select import select_reference

        chosen = select_reference(self.store, self.info.track_name, self.info.car_name)
        if chosen is not None:
            row, grid = chosen
            self.coach.reference = grid
            self.coach.reference_label = row.label
            self.coach.reference_is_imported = True
            self._remember_reference()
            from simengineer.reference.delta import LiveDelta

            delta = LiveDelta(grid)
            coach = self.coach

            def on_frame(f: Frame) -> None:
                coach.delta_s = delta.update(f)

            self.add_frame_hook(on_frame)
            log.info("Référence : %s (%.3f s)", row.label, grid.lap_time)

    def _remember_reference(self) -> None:
        ref = self.coach.reference
        if ref is not None:
            self.store.kv_set(
                f"session_ref:{self.session_id}",
                {"label": self.coach.reference_label, "lap_s": round(ref.lap_time, 3)},
            )

    # --- boucle
    def run(self, max_frames: int | None = None) -> int:
        """Traite la session jusqu'à la fin de la source. Renvoie l'identifiant de session."""
        self._setup()
        assert self.engineer is not None
        engineer, spotter, arbiter = self.engineer, self.spotter, self.arbiter
        segmenter = LapSegmenter(self.info.track_length_m)
        ctx = SpeechContext()
        perf = time.perf_counter
        try:
            for f in self.source.frames():
                stamp = perf()
                self._session_clock = f.session_time
                if spotter is not None:
                    for utt in spotter.on_frame(f, stamp):
                        arbiter.submit(utt)
                    ctx.in_battle = spotter.in_battle
                lap = segmenter.on_frame(f)
                for utt in engineer.on_frame(f):
                    arbiter.submit(utt)
                if lap is not None:
                    self._on_lap(lap)
                for hook in self._frame_hooks:
                    hook(f)
                ctx.braking = f.brake > 0.1
                ctx.on_straight = abs(f.steering) < 0.15 and f.throttle > 0.5
                arbiter.tick(ctx)
                self.frames += 1
                if self.frames % _POLL_EVERY == 0:
                    self._poll()
                elapsed = perf() - stamp
                self.total_frame_s += elapsed
                if elapsed * 1000.0 > self.max_frame_ms:
                    self.max_frame_ms = elapsed * 1000.0
                if max_frames is not None and self.frames >= max_frames:
                    break
            last = segmenter.flush()
            if last is not None and last.channels["time"].size > 60:
                self._on_lap(last, announce=False)
        finally:
            self._finish()
        return self.session_id

    def _on_lap(self, lap: Lap, announce: bool = True) -> None:
        assert self.engineer is not None
        if announce:
            for utt in self.engineer.on_lap(lap):
                self.arbiter.submit(utt)
        npz = self.store.lap_path(self.session_id, lap.number)
        rel = str(npz.relative_to(self.store.data_dir))
        coach = self.coach
        self.worker.submit(
            process_lap, lap, coach.reference, coach.corners or None, self.cfg.coach,
            self.cfg.language, coach.reference_label, npz, rel,
        )  # fmt: skip

    def _poll(self) -> None:
        for result in self.worker.poll():
            self._on_result(result)
        for hook in self._poll_hooks:
            hook()

    def _on_result(self, res: LapResult, announce: bool = True) -> None:
        lap, coach = res.lap, self.coach
        lap_id = self.store.add_lap_row(self.session_id, lap, res.npz_path)
        self._lap_ids[lap.number] = lap_id
        if res.corners and not coach.corners:
            coach.corners = res.corners
            self.store.kv_set(
                trackmap_key(self.info.track_name), [c.to_dict() for c in res.corners]
            )
        if res.analysis is not None:
            coach.last_analysis = res.analysis
            self.store.add_corner_results(self.session_id, lap_id, corner_rows(res.analysis))
            if announce and self.cfg.coach.enabled:
                for utt in debrief_utterances(res.analysis, self.cfg.coach):
                    self.arbiter.submit(utt)
        if lap.valid and not coach.reference_is_imported:
            # Sans référence importée : le meilleur tour de la séance sert de référence.
            ref = coach.reference
            if ref is None or lap.lap_time < ref.lap_time:
                coach.reference = res.grid
                coach.reference_label = (
                    "meilleur tour de la séance" if self.cfg.language == "fr" else "session best"
                )
                self._remember_reference()
        self.bus.publish(LapCompleted(lap, lap_id))

    def _finish(self) -> None:
        try:
            for result in self.worker.drain():
                self._on_result(result, announce=False)
        finally:
            self.worker.close()
            if self.spotter is not None:
                self.spotter.close()
            self.source.close()
            self.bus.publish(SessionEnded(self.session_id))

    def stats(self) -> dict[str, Any]:
        mean_ms = self.total_frame_s / self.frames * 1000.0 if self.frames else 0.0
        return {
            "frames": self.frames,
            "mean_frame_ms": round(mean_ms, 4),
            "max_frame_ms": round(self.max_frame_ms, 3),
            "cpu_share_at_60hz_pct": round(mean_ms * 60.0 / 10.0, 3),
        }
