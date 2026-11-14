from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from simengineer.app import SimEngineerApp
from simengineer.cli import app as cli_app
from simengineer.core.config import AppConfig
from simengineer.reference.delta import LiveDelta
from simengineer.reference.importer import (
    ReferenceImportError,
    csv_to_grid,
    import_csv,
    import_ibt,
)
from simengineer.reference.select import select_reference
from simengineer.reference.store import (
    ReferenceRightsError,
    delete_reference,
    list_references,
)
from simengineer.sources.ibt_format import write_ibt
from simengineer.sources.synthetic import (
    DEFAULT_TRACK,
    CornerStyle,
    Scenario,
    SyntheticSource,
    build_lap_profile,
    generate,
    lap_style,
)
from simengineer.store.db import Store
from simengineer.telemetry.resample import GridLap

IDEAL = build_lap_profile(DEFAULT_TRACK)
SLOW = lap_style(c2=CornerStyle(brake_offset_m=-15.0), c5=CornerStyle(throttle_offset_m=14.0))
CAR = "Porsche 911 GT3 Cup (992)"


def make_ibt(tmp_path: Path) -> Path:
    path = tmp_path / "pro.ibt"
    write_ibt(generate(Scenario(n_laps=3)), path)
    return path


def write_csv(path: Path, *, kmh: bool = True, pct: bool = False, time: bool = False) -> None:
    p = IDEAL
    idx = slice(None, None, 4)  # un point tous les 2 m
    head = ["LapDistPct" if pct else "Distance (m)", "Speed (km/h)" if kmh else "Speed",
            "Throttle", "Brake", "Steering"]  # fmt: skip
    cols = [p.s[idx] / DEFAULT_TRACK.length_m if pct else p.s[idx],
            p.v[idx] * (3.6 if kmh else 1.0), p.throttle[idx] * 100.0, p.brake[idx] * 100.0,
            np.degrees(p.steering[idx])]  # fmt: skip
    if time:
        head.append("Time")
        cols.append(p.t[:-1][idx])
    lines = [",".join(head)] + [
        ",".join(f"{v:.5f}" for v in row) for row in zip(*cols, strict=True)
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_rights_metadata_are_mandatory(tmp_path: Path) -> None:
    store = Store(tmp_path / "d")
    ibt = make_ibt(tmp_path)
    with pytest.raises(ReferenceRightsError):
        import_ibt(store, ibt, source="", rights="personnel")
    with pytest.raises(ReferenceRightsError):
        import_ibt(store, ibt, source="mon tour", rights="  ")
    assert list_references(store) == []


def test_import_ibt_picks_best_valid_lap(tmp_path: Path) -> None:
    store = Store(tmp_path / "d")
    row = import_ibt(store, make_ibt(tmp_path), "mon meilleur tour", "données personnelles")
    assert row.track == "Synthetic Ring" and row.car == CAR
    assert row.lap_time == pytest.approx(IDEAL.lap_time, abs=0.01)
    assert row.source == "mon meilleur tour" and row.rights == "données personnelles"
    grid = store.load_grid(row.npz_path)
    assert grid.n == 4200 and grid.meta["rights"] == "données personnelles"
    with pytest.raises(ReferenceImportError):
        import_ibt(store, make_ibt(tmp_path), "x", "y", lap_number=99)


@pytest.mark.parametrize(("kmh", "pct", "time"), [(True, False, False), (False, True, True)])
def test_csv_import_units_and_missing_time(
    tmp_path: Path, kmh: bool, pct: bool, time: bool
) -> None:
    path = tmp_path / "lap.csv"
    write_csv(path, kmh=kmh, pct=pct, time=time)
    grid = csv_to_grid(path, DEFAULT_TRACK.length_m if pct else None)
    assert grid.n == 4200
    assert grid.lap_time == pytest.approx(IDEAL.lap_time, abs=0.15)
    assert grid.ch("speed").max() < 80 and grid.ch("brake").max() <= 1.0
    assert abs(grid.ch("steering")).max() < 2.0  # degrés convertis en radians
    assert grid.ch("speed")[1500] == pytest.approx(IDEAL.v[3000], abs=0.5)


def test_csv_import_errors(tmp_path: Path) -> None:
    store = Store(tmp_path / "d")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")
    with pytest.raises(ReferenceImportError):
        csv_to_grid(bad)
    path = tmp_path / "lap.csv"
    write_csv(path, pct=True)
    with pytest.raises(ReferenceImportError):
        csv_to_grid(path)  # en % du tour : longueur obligatoire
    with pytest.raises(ReferenceImportError):
        import_csv(store, path, "", CAR, "pack", "licence", track_length_m=4200.0)


def test_selection_same_track_same_car(tmp_path: Path) -> None:
    store = Store(tmp_path / "d")
    path = tmp_path / "lap.csv"
    write_csv(path)
    import_csv(store, path, "Synthetic Ring", "Autre voiture", "pack", "licence")
    import_csv(store, path, "Autre circuit", CAR, "pack", "licence")
    assert select_reference(store, "Synthetic Ring", CAR) is None
    mine = import_csv(store, path, "Synthetic Ring", CAR, "pack acheté", "licence perso", "Pro A")
    chosen = select_reference(store, "Synthetic Ring", CAR)
    assert chosen is not None and chosen[0].id == mine.id and chosen[0].label == "Pro A"
    delete_reference(store, mine.id)
    assert select_reference(store, "Synthetic Ring", CAR) is None
    assert not (store.data_dir / mine.npz_path).exists()


def ref_grid(tmp_path: Path) -> GridLap:
    store = Store(tmp_path / "r")
    return store.load_grid(import_ibt(store, make_ibt(tmp_path), "moi", "perso").npz_path)


def test_live_delta_matches_lap_time_difference(tmp_path: Path) -> None:
    delta = LiveDelta(ref_grid(tmp_path))
    slow_time = build_lap_profile(DEFAULT_TRACK, SLOW).lap_time
    values: dict[int, list[float]] = {}
    for f in SyntheticSource(Scenario(n_laps=3, lap_styles=((1, SLOW),))).frames():
        d = delta.update(f)
        if d is not None:
            values.setdefault(f.lap, []).append(d)
    assert max(abs(v) for v in values[1]) < 0.03  # tour idéal : delta nul
    assert values[2][0] == pytest.approx(0.0, abs=0.03)
    assert values[2][-1] == pytest.approx(slow_time - IDEAL.lap_time, abs=0.03)
    mid = values[2][len(values[2]) // 2]  # après le T03 (freinage trop tôt), avant le T06
    assert 0.3 < mid < values[2][-1]


def test_live_delta_unknown_after_teleport_and_in_pits(tmp_path: Path) -> None:
    delta = LiveDelta(ref_grid(tmp_path))
    frames = list(SyntheticSource(Scenario(n_laps=2)).frames())
    assert LiveDelta(ref_grid(tmp_path)).update(frames[3000]) is None  # démarrage en plein tour
    for f in frames[:600]:
        assert delta.update(f) is not None
    assert delta.update(frames[3000]) is None
    f = frames[5600]
    assert delta.update(f) is None  # toujours inconnu jusqu'au prochain passage de ligne


def test_app_uses_imported_reference(cfg: AppConfig, tmp_path: Path) -> None:
    cfg.source.realtime = False
    store = Store(cfg.data_dir)
    import_ibt(store, make_ibt(tmp_path), "pack acheté", "licence perso", label="Tour pro")
    app = SimEngineerApp(cfg, SyntheticSource(Scenario(n_laps=4, base_style=SLOW)), store=store)
    sid = app.run()
    assert app.coach.reference_is_imported and app.coach.reference_label == "Tour pro"
    assert app.coach.delta_s is not None and app.coach.delta_s > 0.4
    analysis = app.coach.last_analysis
    assert analysis is not None and analysis.ref_label == "Tour pro"
    assert [(a.corner, a.issue) for a in analysis.advice] == [
        (3, "brake_later"),
        (6, "throttle_earlier"),
    ]
    assert store.kv_get(f"session_ref:{sid}")["label"] == "Tour pro"
    spoken = [u.text for u in app.arbiter.spoken if u.priority == 2]
    assert "Virage 3, freine 13 mètres plus tard" in spoken


def test_cli_ref_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMENGINEER__LOGGING__LEVEL", "WARNING")
    runner = CliRunner()
    ibt = make_ibt(tmp_path)
    res = runner.invoke(cli_app, ["ref", "import", str(ibt), "--source", "", "--rights", "x"])
    assert res.exit_code == 1 and "Import refusé" in res.output
    res = runner.invoke(
        cli_app, ["ref", "import", str(ibt), "--source", "mon tour", "--rights", "personnel"]
    )
    assert res.exit_code == 0 and "Référence 1 importée" in res.output
    res = runner.invoke(cli_app, ["ref", "list"])
    assert "droits: personnel" in res.output
    assert runner.invoke(cli_app, ["ref", "delete", "1"]).exit_code == 0
    assert "Aucune référence" in runner.invoke(cli_app, ["ref", "list"]).output
