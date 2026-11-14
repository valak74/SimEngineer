"""Création de la source de télémétrie selon la configuration."""

from __future__ import annotations

from pathlib import Path

from simengineer.core.config import SourceConfig
from simengineer.sources.base import TelemetrySource


def make_source(cfg: SourceConfig) -> TelemetrySource:
    if cfg.kind == "synthetic":
        from simengineer.sources.scenarios import get_scenario
        from simengineer.sources.synthetic import SyntheticSource

        return SyntheticSource(get_scenario(cfg.scenario), cfg.realtime, cfg.speed)
    if cfg.kind == "ibt_replay":
        from simengineer.sources.ibt_replay import IbtReplaySource

        if not cfg.ibt_path:
            raise ValueError("source.ibt_path est requis pour la source ibt_replay")
        return IbtReplaySource(Path(cfg.ibt_path), cfg.realtime, cfg.speed)
    from simengineer.sources.live import LiveSource

    return LiveSource(wait_for_sim=cfg.wait_for_sim)
