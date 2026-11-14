"""Source live et commandes de stand testées avec un FAUX module pyirsdk.

Cela valide notre code contre l'API pyirsdk SUPPOSÉE (docs/ASSUMPTIONS.md, section C),
pas contre iRacing : la vérification réelle reste à faire sur le PC (docs/RUN_ON_PC.md).
"""

from __future__ import annotations

import sys
import types
from typing import Any, ClassVar

import pytest

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.core.vars import VARS
from simengineer.engineer.pit_commands import IrsdkPitCommander, make_pit_commander
from simengineer.sources import live
from simengineer.sources.base import RawSession
from simengineer.sources.dump import write_vars_doc
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import generate


class FakeIRSDK:
    raw: RawSession
    starts_needed = 0
    instances: ClassVar[list[FakeIRSDK]] = []

    def __init__(self) -> None:
        self.i = -1
        self.is_initialized = False
        self.is_connected = False
        self.commands: list[tuple[int, int]] = []
        self._starts = 0
        self.shut = False
        FakeIRSDK.instances.append(self)

    def startup(self) -> bool:
        self._starts += 1
        ok = self._starts > self.starts_needed
        self.is_initialized = self.is_connected = ok
        return ok

    def shutdown(self) -> None:
        self.shut = True

    @property
    def var_headers_names(self) -> list[str]:
        return list(self.raw.columns)

    def freeze_var_buffer_latest(self) -> None:
        self.i += 1
        if self.i >= self.raw.n_frames - 1:
            self.is_connected = False

    def unfreeze_var_buffer_latest(self) -> None:
        return None

    def __getitem__(self, name: str) -> Any:
        info = self.raw.info
        if name == "WeekendInfo":
            return {"TrackDisplayName": info.track_name, "TrackLength": "4.2000 km"}
        if name == "DriverInfo":
            return {
                "DriverCarIdx": 0,
                "DriverCarFuelMaxLtr": info.fuel_capacity_l,
                "Drivers": [{"CarIdx": 0, "UserName": "Moi", "CarScreenName": info.car_name}],
            }
        if name == "SessionInfo":
            return {"Sessions": [{"SessionNum": 0, "SessionType": "Race", "SessionLaps": "12",
                                  "SessionTime": "unlimited"}]}  # fmt: skip
        col = self.raw.columns[name][max(self.i, 0)]
        return col.tolist() if hasattr(col, "tolist") else col

    def pit_command(self, mode: int, var: int = 0) -> None:
        self.commands.append((mode, var))


class FakePitCommandMode:
    clear, ws, fuel, lf, rf, lr, rr, clear_tires = range(8)


@pytest.fixture
def fake_irsdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeIRSDK]:
    module = types.ModuleType("irsdk")
    module.IRSDK = FakeIRSDK  # type: ignore[attr-defined]
    module.PitCommandMode = FakePitCommandMode  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "irsdk", module)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    FakeIRSDK.raw = generate(get_scenario("overtake"))
    FakeIRSDK.starts_needed = 0
    FakeIRSDK.instances = []
    return FakeIRSDK


def test_live_source_reads_frames_until_disconnect(fake_irsdk: type[FakeIRSDK]) -> None:
    fake_irsdk.starts_needed = 2  # iRacing pas encore lancé : on attend
    src = live.LiveSource(wait_for_sim=True, poll_s=0.0)
    src.open()
    info = src.session_info()
    assert info.track_name == "Synthetic Ring" and info.track_length_m == 4200.0
    assert info.session_laps == 12 and info.is_race and info.fuel_capacity_l == 100.0
    frames = list(src.frames())
    assert len(frames) >= fake_irsdk.raw.n_frames - 1
    assert frames[10].car_idx_lap_dist_pct is not None and frames[0].slow is not None
    assert {d.name for d in src.available_vars()} >= {VARS["speed"].name}
    src.close()
    assert fake_irsdk.instances[0].shut


def test_live_source_without_sim(fake_irsdk: type[FakeIRSDK]) -> None:
    fake_irsdk.starts_needed = 10**9
    with pytest.raises(live.SimNotRunningError):
        live.LiveSource(wait_for_sim=False).open()


def test_dump_vars_reports_missing_names(fake_irsdk: type[FakeIRSDK], tmp_path: Any) -> None:
    del fake_irsdk.raw.columns[VARS["car_left_right"].name]  # variable absente du « vrai » sim
    src = live.LiveSource()
    src.open()
    report = write_vars_doc(src, tmp_path / "vars.md")
    assert report.ok and VARS["car_left_right"].name in report.missing_optional
    assert "| `car_left_right` | `CarLeftRight` | non | **NON** |" in (
        tmp_path / "vars.md"
    ).read_text(encoding="utf-8")
    frames = src.frames()
    assert next(frames).car_left_right == 0  # facultative absente : valeur neutre, pas de crash


def test_app_runs_on_live_source_and_never_writes_by_default(
    fake_irsdk: type[FakeIRSDK], cfg: AppConfig
) -> None:
    cfg.source.kind = "live"
    cfg.source.realtime = False
    app = SimEngineerApp(cfg, live.LiveSource())
    app.run()
    sdk = fake_irsdk.instances[0]
    assert app.frames > 5000 and any(u.key == "car_right" for u in app.arbiter.spoken)
    assert app.engineer is not None
    assert app.engineer.set_pit_fuel(20).startswith("Commandes de stand désactivées")
    assert sdk.commands == []  # règle 1 : aucune écriture vers le sim par défaut


def test_pit_commands_use_sdk_broadcast_when_enabled(fake_irsdk: type[FakeIRSDK]) -> None:
    src = live.LiveSource()
    src.open()
    cmd = make_pit_commander(True, src)
    assert isinstance(cmd, IrsdkPitCommander)
    assert cmd.set_fuel(17.2).params == {"liters": 18}
    assert cmd.set_tyres(True).ok and cmd.set_tyres(False).key == "pit_cmd_tyres_off"
    m = FakePitCommandMode
    assert fake_irsdk.instances[0].commands == [
        (m.fuel, 18), (m.lf, 0), (m.rf, 0), (m.lr, 0), (m.rr, 0), (m.clear_tires, 0),
    ]  # fmt: skip
