"""Mesure du budget CPU/RAM du collecteur (règle 4) en conditions temps réel.

Mesure le processus principal (lecture 60 Hz + spotter + ingénieur + arbitre). Le worker
d'analyse (processus basse priorité) est mesuré à part. Hors modèles vocaux : voix « null ».
"""

from __future__ import annotations

import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.sources.factory import make_source
from simengineer.voice.speaker import NullSpeaker

CPU_BUDGET_PCT = 3.0
RAM_BUDGET_MB = 150.0


@dataclass(slots=True)
class PerfResult:
    source: str
    seconds: float
    frames: int
    cpu_pct_of_one_core: float
    rss_mb: float
    mean_frame_ms: float
    max_frame_ms: float
    worker_cpu_s: float
    worker_rss_mb: float
    alerts: int
    platform: str
    cpu: str
    python: str

    @property
    def within_budget(self) -> bool:
        return self.cpu_pct_of_one_core < CPU_BUDGET_PCT and self.rss_mb < RAM_BUDGET_MB

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "within_budget": self.within_budget}


def _rss_mb() -> float:
    try:
        import psutil

        return float(psutil.Process().memory_info().rss) / 1e6
    except ImportError:
        if sys.platform == "win32":
            return float("nan")
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def _children() -> tuple[float, float]:
    """(temps CPU, RSS en Mo) cumulés des processus enfants (worker d'analyse)."""
    try:
        import psutil
    except ImportError:
        return 0.0, 0.0
    cpu = rss = 0.0
    for child in psutil.Process().children(recursive=True):
        try:
            times = child.cpu_times()
            cpu += times.user + times.system
            rss += child.memory_info().rss / 1e6
        except psutil.Error:
            continue
    return cpu, rss


def _cpu_name() -> str:
    name = platform.processor()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return name or "inconnu"


def measure(cfg: AppConfig, seconds: float = 30.0) -> PerfResult:
    """Fait tourner l'application en temps réel pendant `seconds` et mesure le processus."""
    cfg = cfg.model_copy(deep=True)
    cfg.source.realtime = True
    cfg.voice.tts_backend = "null"
    cfg.voice.ptt_kind = "none"
    app = SimEngineerApp(cfg, make_source(cfg.source), NullSpeaker())
    worker = {"cpu": 0.0, "rss": 0.0}
    rss = {"max": 0.0}

    def sample() -> None:
        if app.frames % 120 == 0:  # toutes les 2 s
            rss["max"] = max(rss["max"], _rss_mb())
            cpu_s, rss_mb = _children()
            worker["cpu"], worker["rss"] = max(worker["cpu"], cpu_s), max(worker["rss"], rss_mb)

    app.add_poll_hook(sample)
    cpu0, wall0 = time.process_time(), time.perf_counter()
    app.run(max_frames=int(seconds * 60))
    cpu1, wall1 = time.process_time(), time.perf_counter()
    stats = app.stats()
    wall = wall1 - wall0
    return PerfResult(
        source=cfg.source.kind,
        seconds=round(wall, 1),
        frames=stats["frames"],
        cpu_pct_of_one_core=round((cpu1 - cpu0) / wall * 100.0, 2),
        rss_mb=round(max(rss["max"], _rss_mb()), 1),
        mean_frame_ms=stats["mean_frame_ms"],
        max_frame_ms=stats["max_frame_ms"],
        worker_cpu_s=round(worker["cpu"], 2),
        worker_rss_mb=round(worker["rss"], 1),
        alerts=app.spotter.alert_count if app.spotter else 0,
        platform=f"{platform.system()} {platform.release()}",
        cpu=f"{_cpu_name()} ({os.cpu_count()} cœurs logiques)",
        python=platform.python_version(),
    )


def render(result: PerfResult) -> str:
    ok = "OUI" if result.within_budget else "NON"
    return "\n".join(
        [
            f"Source                : {result.source} ({result.seconds} s, {result.frames} images)",
            f"CPU collecteur        : {result.cpu_pct_of_one_core} % d'un cœur"
            f" (budget < {CPU_BUDGET_PCT} %)",
            f"RAM collecteur (RSS)  : {result.rss_mb} Mo (budget < {RAM_BUDGET_MB:.0f} Mo)",
            f"Temps par image       : {result.mean_frame_ms} ms en moyenne,"
            f" {result.max_frame_ms} ms au pire",
            f"Worker d'analyse      : {result.worker_cpu_s} s CPU cumulées,"
            f" {result.worker_rss_mb} Mo (processus séparé, basse priorité)",
            f"Alertes spotter       : {result.alerts}",
            f"Machine               : {result.platform}, {result.cpu}, Python {result.python}",
            f"Dans le budget        : {ok}",
        ]
    )
