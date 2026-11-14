from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.coach.trackmap import (
    TrackFileError,
    export_track_file,
    load_corners,
    read_track_file,
    slug,
    track_file,
)
from simengineer.core.config import AppConfig
from simengineer.sources.synthetic import CornerStyle, Scenario, SyntheticSource, lap_style
from simengineer.store.db import Store
from simengineer.voice.templates import corner_word

TRACK, L = "Synthetic Ring", 4200.0
SLOW_T3 = lap_style(c2=CornerStyle(brake_offset_m=-14.0))


def run(cfg: AppConfig, sc: Scenario) -> SimEngineerApp:
    cfg.source.realtime = False
    app = SimEngineerApp(cfg, SyntheticSource(sc))
    app.run()
    return app


def name_corner(path: Path, number: int, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    blocks = text.split("[[corners]]")
    blocks[number] = blocks[number].replace('label = ""', f'label = "{label}"', 1)
    path.write_text("[[corners]]".join(blocks), encoding="utf-8")


def test_corner_word() -> None:
    assert corner_word(3) == "Virage 3" and corner_word(3, "en") == "Turn 3"
    assert corner_word(1, "fr", "La Source") == "La Source"
    assert corner_word(1, "fr", "Raidillon de l'Eau Rouge") == "Virage 1"  # trop long à dire
    assert slug("Spa-Francorchamps - Grand Prix") == "spa_francorchamps_grand_prix"


def test_export_edit_and_use_labels_everywhere(cfg: AppConfig) -> None:
    run(cfg, Scenario(n_laps=3))
    store = Store(cfg.data_dir)
    path = export_track_file(store, TRACK, L)
    assert path == track_file(cfg.data_dir, TRACK) and "[[corners]]" in path.read_text("utf-8")
    with pytest.raises(FileExistsError):
        export_track_file(store, TRACK, L)
    name_corner(path, 3, "La Source")
    corners = load_corners(store, TRACK, L)
    assert corners is not None and corners[2].label == "La Source"
    assert corners[2].display == "T03 La Source"
    app = run(cfg, Scenario(n_laps=4, lap_styles=((1, SLOW_T3), (2, SLOW_T3))))
    spoken = [u.text for u in app.arbiter.spoken if u.key.startswith("adv:T03")]
    assert spoken == ["La Source, freine 12 mètres plus tard"] * len(spoken) and spoken
    analysis = app.coach.last_analysis
    assert analysis is not None and analysis.advice[0].long.startswith("T03 La Source : freine")


def test_track_file_can_merge_and_drop_corners(cfg: AppConfig) -> None:
    run(cfg, Scenario(n_laps=2))
    store = Store(cfg.data_dir)
    path = export_track_file(store, TRACK, L)
    blocks = path.read_text("utf-8").split("[[corners]]")
    path.write_text("[[corners]]".join(blocks[:1] + blocks[2:]), encoding="utf-8")  # sans T01
    corners = load_corners(store, TRACK, L)
    assert corners is not None and len(corners) == 7
    assert corners[0].number == 1 and corners[0].zone_start_m == 0.0  # renuméroté
    assert corners[-1].zone_end_m == L
    assert all(a.zone_end_m == b.zone_start_m for a, b in pairwise(corners))


def test_invalid_track_file_falls_back(cfg: AppConfig, tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[[corners]]\nstart_m = 500\napex_m = 400\nend_m = 600\n", encoding="utf-8")
    with pytest.raises(TrackFileError):
        read_track_file(bad, L)
    bad.write_text("pas du toml [[", encoding="utf-8")
    with pytest.raises(TrackFileError):
        read_track_file(bad, L)
    run(cfg, Scenario(n_laps=2))
    store = Store(cfg.data_dir)
    path = track_file(cfg.data_dir, TRACK)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[[corners]]\nlabel = 'x'\n", encoding="utf-8")
    corners = load_corners(store, TRACK, L)  # fichier invalide : détection automatique
    assert corners is not None and len(corners) == 8 and not corners[0].label


def test_cli_track_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMENGINEER__LOGGING__LEVEL", "WARNING")
    runner = CliRunner()
    assert runner.invoke(cli_app, ["track", "show"]).exit_code == 1
    args = [
        "run",
        "-s",
        "synthetic",
        "--scenario",
        "clean",
        "--no-realtime",
        "--null-voice",
        "--no-ui",
    ]
    assert runner.invoke(cli_app, args).exit_code == 0
    res = runner.invoke(cli_app, ["track", "show"])
    assert res.exit_code == 0 and "détection automatique" in res.output and "T08" in res.output
    res = runner.invoke(cli_app, ["track", "export"])
    assert res.exit_code == 0 and "synthetic_ring.toml" in res.output
    assert runner.invoke(cli_app, ["track", "export"]).exit_code == 1
    assert "fichier" in runner.invoke(cli_app, ["track", "show"]).output
