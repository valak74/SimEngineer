from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.core.config import AppConfig, load_config
from simengineer.core.config_edit import set_config_value
from simengineer.diagnostic import DIAG_REV, DONE_KEY, DiagnosticRecorder, diagnostic_needed
from simengineer.runtime import attach_diagnostic
from simengineer.sources.ibt_replay import IbtReplaySource
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import Scenario, SyntheticSource
from simengineer.telemetry.laps import LapSegmenter
from simengineer.voice.download import download_voices, voice_files
from simengineer.voice.ptt_detect import Detected, detect
from simengineer.voice.speaker import NullSpeaker


def run_with_diag(cfg: AppConfig, tmp_path: Path, sc: Scenario) -> tuple[SimEngineerApp, Path]:
    cfg.source.realtime = False
    app = SimEngineerApp(cfg, SyntheticSource(sc), NullSpeaker())
    rec = DiagnosticRecorder(cfg.data_dir / "diagnostic", tmp_path / "Bureau")
    rec.attach(app)
    rec.record_latency("car_left", 0, 42.0)
    rec.record_latency("car_left", 0, 61.0)
    app.run()
    assert rec.zip_path is not None
    return app, rec.zip_path


def test_diagnostic_zip_is_complete_and_replayable(cfg: AppConfig, tmp_path: Path) -> None:
    sc = dataclasses.replace(get_scenario("race"), n_laps=5, race_laps=5, incident_limit=17)
    app, zip_path = run_with_diag(cfg, tmp_path, sc)
    assert zip_path.parent == tmp_path / "Bureau" and zip_path.name.startswith("SimEngineer-diag")
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        assert {"rapport.md", "irsdk_vars.md", "echantillon.ibt", "alertes.jsonl",
                "config.json", "journal.txt"} <= names  # fmt: skip
        report = z.read("rapport.md").decode("utf-8")
        assert 'api_key":' not in z.read("config.json").decode("utf-8").replace("api_key_env", "")
        z.extract("echantillon.ibt", tmp_path)
    # le générateur synthétique n'émet pas SessionNum ni CarIdxLap : signalé, pas bloquant
    assert "| ATTENTION | Variables de VARS | absentes : SessionNum, CarIdxLap |" in report
    assert "| OK | Temps au tour recalculés vs iRacing | 4 tours, écart max 0 ms |" in report
    assert "| OK | Latence voix P0 | médiane 52 ms" in report
    assert "incidents=17" in report and "Lat/Lon en direct | présentes" in report
    assert app.store.kv_get(DONE_KEY) == DIAG_REV and not diagnostic_needed(app.store)
    # L'échantillon se rejoue : 3 tours complets, pilotes anonymisés
    src = IbtReplaySource(tmp_path / "echantillon.ibt")
    info = src.session_info()
    assert info.track_name == "Synthetic Ring" and info.incident_limit == 17
    assert all(name.startswith("Pilote ") for name in info.drivers.values())
    seg = LapSegmenter(info.track_length_m)
    laps = [lap for f in src.frames() if (lap := seg.on_frame(f)) is not None]
    assert sum(lap.complete for lap in laps) == 3


def test_diagnostic_only_once_in_auto_mode(cfg: AppConfig) -> None:
    cfg.source.kind = "live"
    app = SimEngineerApp(cfg, SyntheticSource(Scenario(n_laps=1)), NullSpeaker())
    assert attach_diagnostic(app) is not None  # première séance réelle
    app.store.kv_set(DONE_KEY, DIAG_REV)
    assert attach_diagnostic(app) is None  # déjà fait
    assert attach_diagnostic(app, force=True) is not None
    cfg.diagnostic.mode = "off"
    app.store.kv_set(DONE_KEY, 0)
    assert attach_diagnostic(app) is None
    cfg.source.kind = "synthetic"
    cfg.diagnostic.mode = "auto"
    assert attach_diagnostic(app) is None  # jamais en simulé sans le demander


def test_config_edit_keeps_file_valid(tmp_path: Path) -> None:
    toml = tmp_path / "simengineer.toml"
    toml.write_text(
        (Path(__file__).parent.parent / "simengineer.example.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    set_config_value(toml, "voice", "ptt_kind", "joystick")
    set_config_value(toml, "voice", "ptt_button", 7)
    set_config_value(toml, "source", "kind", "live")
    set_config_value(toml, "llm", "daily_budget_usd", 0.2)
    set_config_value(toml, "diagnostic", "mode", "off")  # section absente : créée
    set_config_value(toml, "voice", "output_device", 'Casque "Pro"')
    text = toml.read_text(encoding="utf-8")
    assert text.count("ptt_kind") == 1 and "vide = défaut système" in text  # commentaire gardé
    cfg = load_config(toml, env={})
    assert cfg.voice.ptt_kind == "joystick" and cfg.voice.ptt_button == 7
    assert cfg.source.kind == "live" and cfg.llm.daily_budget_usd == 0.2
    assert cfg.diagnostic.mode == "off" and cfg.voice.output_device == 'Casque "Pro"'
    assert [p.name for p in cfg.llm.providers] == ["qwen", "deepseek"]  # tableaux intacts


def test_voice_download(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str, target: Path) -> None:
        calls.append(url)
        if "en_US" in url:
            raise OSError("hors ligne")
        target.write_bytes(b"x")

    present = download_voices(tmp_path, fetch=fetch, echo=lambda _: None)
    assert [p.name for p in present] == ["fr_FR-siwis-medium.onnx", "fr_FR-siwis-medium.onnx.json"]
    assert calls[0].endswith("/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx")
    assert not list(tmp_path.glob("*.part"))
    calls.clear()
    download_voices(tmp_path, ("fr",), fetch=fetch, echo=lambda _: None)
    assert calls == []  # déjà présentes : rien à retélécharger
    assert voice_files("en", tmp_path)[1][1].name == "en_US-lessac-medium.onnx.json"


def test_ptt_detection_logic() -> None:
    hits = iter([None, None, Detected("joystick", 1, 5, device="Volant")])
    assert detect([lambda: next(hits)], timeout_s=2.0, poll_s=0.0) == Detected(
        "joystick", 1, 5, device="Volant"
    )
    assert detect([lambda: None], timeout_s=0.05, poll_s=0.01) is None


def test_cli_config_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(cli_app, ["config-set", "voice", "ptt_key", "f10"])
    assert res.exit_code == 0
    assert load_config(tmp_path / "simengineer.toml", env={}).voice.ptt_key == "f10"
