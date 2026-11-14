from __future__ import annotations

from pathlib import Path

import pytest

from simengineer.core.config import AppConfig


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    c = AppConfig()
    c.data_dir = tmp_path / "data"
    c.voice.tts_backend = "null"
    c.voice.ptt_kind = "none"
    c.voice.stt_backend = "none"
    return c
