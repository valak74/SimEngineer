"""Source `ibt_replay` : rejoue un fichier de télémétrie .ibt du pilote."""

from __future__ import annotations

from pathlib import Path

from simengineer.sources.base import RawSessionSource
from simengineer.sources.ibt_format import read_ibt


class IbtReplaySource(RawSessionSource):
    def __init__(self, path: Path, realtime: bool = False) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Fichier .ibt introuvable : {path}")
        super().__init__(read_ibt(path), "ibt_replay", realtime)
        self.path = path
