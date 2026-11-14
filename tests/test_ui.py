from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.core.vars import Flags
from simengineer.setup.compare import add_mark
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import SyntheticSource
from simengineer.store.db import Store
from simengineer.ui.live import flag_state, waiting_snapshot
from simengineer.ui.server import UIServer


def get(server: UIServer, path: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=10) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def get_json(server: UIServer, path: str) -> Any:
    code, body, ctype = get(server, path)
    assert code == 200 and ctype.startswith("application/json"), (path, code, body[:200])
    return json.loads(body)


@pytest.fixture
def served(cfg: AppConfig) -> Any:
    cfg.source.realtime = False
    cfg.ui.port = 0  # port libre choisi par le système
    server = UIServer(cfg, inline_queries=True)
    server.start()
    yield cfg, server
    server.stop()


def test_defaults_are_local_only() -> None:
    cfg = AppConfig()
    assert cfg.ui.host == "127.0.0.1" and cfg.ui.enabled and cfg.ui.refresh_hz == 4.0


def test_static_files_and_security(served: Any) -> None:
    _, server = served
    code, body, ctype = get(server, "/")
    assert code == 200 and ctype.startswith("text/html") and b"SIM<b>ENGINEER</b>" in body
    assert b"http" not in body.replace(b"http://www.w3.org", b"")  # aucune ressource externe
    for path, kind in (("/app.css", "text/css"), ("/app.js", "application/javascript"),
                       ("/favicon.svg", "image/svg+xml")):  # fmt: skip
        code, body, ctype = get(server, path)
        assert code == 200 and ctype.startswith(kind) and body
    assert b"innerHTML" not in get(server, "/app.js")[1]  # rendu sans injection HTML
    for path in ("/../pyproject.toml", "/static/../server.py", "/etc/passwd", "/api/nope"):
        assert get(server, path)[0] == 404
    assert get_json(server, "/api/live") == waiting_snapshot()
    assert get_json(server, "/api/sessions") == []
    assert get_json(server, "/api/profile") is None
    assert get_json(server, "/api/refs") == []
    assert get_json(server, "/api/meta")["language"] == "fr"
    assert get(server, "/api/sessions/999")[0] == 404


def test_live_snapshot_during_race_and_history_pages(served: Any) -> None:
    cfg, server = served
    app = SimEngineerApp(cfg, SyntheticSource(get_scenario("race")))
    server.attach(app)
    snaps: list[dict[str, Any]] = []
    app.add_poll_hook(lambda: snaps.append(server.snapshot))
    sid = app.run()
    live = [s for s in snaps if s.get("status") == "live"]
    assert live, "aucun instantané publié"
    mid = live[len(live) // 2]
    assert mid["session"]["track"] == "Synthetic Ring" and mid["session"]["id"] == sid
    assert {"fuel", "pit", "laps", "gap", "tyres", "weather", "spoken", "car"} <= mid.keys()
    assert mid["fuel"]["avg_per_lap_l"] == 2.5
    assert 0.0 <= mid["car"]["pct"] < 1.0 and mid["car"]["speed_kmh"] > 0
    assert any(s.get("flag") == "yellow" for s in live)
    assert any(s["advice"] for s in live) and any(s["others"] for s in live)
    assert all(m["p"] in (0, 1, 2, 3) for m in mid["spoken"])
    json.dumps(mid, allow_nan=False)  # JSON strict (pas de NaN)
    assert server.snapshot["status"] == "ended"
    # 4 Hz maximum, pas 60 : ~1100 s de course -> < 5000 instantanés
    versions = {s["version"] for s in snaps}
    assert len(versions) < 1150 * 4 + 10
    track = get_json(server, "/api/track")
    assert len(track["points"]) == 525 and len(track["corners"]) == 8
    assert all(0 <= v <= 1000 for p in track["points"] for v in p)
    assert get_json(server, "/api/live")["status"] == "ended"

    sessions = get_json(server, "/api/sessions")
    assert sessions[0]["id"] == sid and sessions[0]["valid"] == 10
    detail = get_json(server, f"/api/sessions/{sid}")
    assert detail["summary"]["laps_total"] == 12 and len(detail["analyses"]) == 9
    first = detail["analyses"][0]
    assert len(first["corners"]) == 8 and first["advice"][0]["text"].startswith("T0")
    assert detail["summary"]["top_corners"][0]["issue_label"]
    prof = get_json(server, "/api/profile")
    assert prof["empty"] is False and prof["plan"]["drills"] and len(prof["corners"]) == 8
    setup = get_json(server, f"/api/setup/{sid}")
    assert setup["laps"] == 10 and set(setup["balance"]) == {"entry", "mid", "exit"}
    assert setup["comparison"] is None and "Save As" in setup["guide"]
    add_mark(Store(cfg.data_dir), sid, "aileron +1")
    assert get_json(server, f"/api/setup/{sid}")["comparison"]["verdict"] == "not_enough_laps"


def test_lap_trace_api(served: Any, tmp_path: Any) -> None:
    from simengineer.sources.synthetic import CornerStyle, Scenario, lap_style

    cfg, server = served
    slow = lap_style(c2=CornerStyle(brake_offset_m=-14.0))
    sc = Scenario(n_laps=5, lap_styles=((1, slow), (3, slow)), offtracks=((2, 0.5, 0.52),))
    sid = SimEngineerApp(cfg, SyntheticSource(sc)).run()
    t = get_json(server, f"/api/sessions/{sid}/laps/2/trace")
    assert t["ref_label"] == "tour 1" and t["step_m"] == 2.0 and t["length_m"] == 4200.0
    n = len(t["delta"])
    assert n == 2100
    for ch in ("speed", "throttle", "brake", "steering", "gear"):
        assert len(t["lap_ch"][ch]) == n and len(t["ref_ch"][ch]) == n
    assert max(t["lap_ch"]["speed"]) < 300 and max(t["lap_ch"]["brake"]) <= 100
    # le delta cumulé vaut 0 au départ et grossit dans la zone du T03
    t03 = next(c for c in t["corners"] if c["name"] == "T03")
    before, after = int(t03["zone_start_m"] / 2) - 1, int(t03["zone_end_m"] / 2) - 1
    assert abs(t["delta"][0]) < 1e-3 and abs(t["delta"][before]) < 0.01
    assert t["delta"][after] == pytest.approx(t03["loss_s"], abs=0.02) and t03["loss_s"] > 0.3
    assert t["advice"][0]["corner"] == "T03"
    best = get_json(server, f"/api/sessions/{sid}/laps/1/trace")  # tour de réf. : vs 2e meilleur
    assert best["ref_label"] == "tour 2"  # 2e meilleur tour valide
    invalid = get_json(server, f"/api/sessions/{sid}/laps/3/trace")  # hors-piste : traçable
    assert invalid["valid"] is False
    assert get(server, f"/api/sessions/{sid}/laps/99/trace")[0] == 404
    assert get(server, f"/api/sessions/{sid}/laps/x/trace")[0] == 404


def test_sse_stream_pushes_snapshots(served: Any) -> None:
    _, server = served
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/live/stream", timeout=10) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        line = r.readline()
        assert line.startswith(b"data: ") and json.loads(line[6:])["status"] == "waiting"
        r.readline()
        server.snapshot = {"status": "live", "version": 42}
        t0 = time.monotonic()
        line = r.readline()
        assert json.loads(line[6:])["version"] == 42 and time.monotonic() - t0 < 2.0


def test_flag_state() -> None:
    assert flag_state(0) == "green"
    assert flag_state(int(Flags.YELLOW_WAVING)) == "yellow"
    assert flag_state(int(Flags.CAUTION | Flags.YELLOW)) == "fcy"
    assert flag_state(int(Flags.CHECKERED | Flags.YELLOW)) == "checkered"
    assert flag_state(int(Flags.BLUE)) == "blue"


def test_history_queries_run_in_low_priority_process(cfg: AppConfig) -> None:
    cfg.ui.port = 0
    server = UIServer(cfg)  # vrai processus séparé
    server.start()
    try:
        assert get_json(server, "/api/sessions") == []
    finally:
        server.stop()
