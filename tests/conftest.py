from __future__ import annotations

from pathlib import Path

import pytest

from simengineer.core.config import AppConfig


@pytest.fixture(autouse=True)
def _no_ui_in_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les tests de la CLI ne démarrent ni serveur d'interface ni navigateur."""
    monkeypatch.setenv("SIMENGINEER__UI__ENABLED", "false")
    monkeypatch.setenv("SIMENGINEER__UI__OPEN_BROWSER", "false")


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    c = AppConfig()
    c.data_dir = tmp_path / "data"
    c.voice.tts_backend = "null"
    c.voice.ptt_kind = "none"
    c.voice.stt_backend = "none"
    return c
