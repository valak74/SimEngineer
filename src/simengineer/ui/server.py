"""Serveur de l'interface : HTTP local (bibliothèque standard), lecture seule.

- `/` et fichiers statiques (HTML/CSS/JS embarqués, aucun CDN : fonctionne hors ligne) ;
- `/api/live` (JSON) et `/api/live/stream` (Server-Sent Events, `refresh_hz` par seconde) ;
- `/api/track`, `/api/sessions`, `/api/sessions/<id>`, `/api/profile`, `/api/setup/<id>`,
  `/api/refs`, `/api/meta`.
Les requêtes d'historique tournent dans un processus à basse priorité.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from multiprocessing import get_context
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from simengineer import __version__
from simengineer.coach.worker import lower_priority
from simengineer.core.config import AppConfig
from simengineer.core.events import SessionEnded
from simengineer.core.log import get_logger
from simengineer.core.types import Frame
from simengineer.llm.tools import ToolRegistry
from simengineer.ui import queries
from simengineer.ui.live import build_snapshot, track_shape, waiting_snapshot

if TYPE_CHECKING:
    from simengineer.app import SimEngineerApp

log = get_logger("ui")

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class QueryRunner:
    """Exécute les requêtes d'historique dans un processus séparé à basse priorité."""

    def __init__(self, inline: bool = False) -> None:
        self._inline = inline
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self._inline:
            return fn(*args)
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=1, mp_context=get_context("spawn"), initializer=lower_priority
                )
            pool = self._pool
        return pool.submit(fn, *args).result(timeout=120)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)


class UIServer:
    def __init__(self, cfg: AppConfig, inline_queries: bool = False) -> None:
        self.cfg = cfg
        self.queries = QueryRunner(inline_queries)
        self.snapshot: dict[str, Any] = waiting_snapshot()
        self.track: dict[str, Any] | None = None
        self._version = 0
        self._track_version = 0
        self._track_ref: object | None = None
        self._httpd: _HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # --- cycle de vie
    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.cfg.ui.port

    @property
    def url(self) -> str:
        host = self.cfg.ui.host
        return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{self.port}/"

    def start(self) -> None:
        self._httpd = _HTTPServer((self.cfg.ui.host, self.cfg.ui.port), _Handler)
        self._httpd.ui = self
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.5}, name="ui", daemon=True
        )
        self._thread.start()
        log.info("Interface : %s", self.url)

    def stop(self) -> None:
        self._stopping.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        self.queries.close()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    # --- branchement sur une session
    def attach(self, app: SimEngineerApp) -> None:
        state: dict[str, Any] = {"frame": None, "next": 0.0, "tools": None}
        period = 1.0 / self.cfg.ui.refresh_hz

        def on_frame(f: Frame) -> None:
            state["frame"] = f

        def on_ready() -> None:
            assert app.engineer is not None
            state["tools"] = ToolRegistry(app.engineer, app.coach, app.cfg.language)
            self._publish(app, state)

        def on_poll() -> None:
            # Cadence calée sur le temps de session : identique en direct et en rejeu.
            f = state["frame"]
            now = f.session_time if f is not None else time.monotonic()
            if now < state["next"] - 1.0:  # retour en arrière (nouvelle session, rejeu)
                state["next"] = now
            if now >= state["next"] and state["tools"] is not None:
                state["next"] = now + period
                self._publish(app, state)

        def on_end(_: SessionEnded) -> None:
            if state["tools"] is not None:
                self._publish(app, state)
            self._version += 1
            self.snapshot = {**self.snapshot, "status": "ended", "version": self._version}

        app.add_frame_hook(on_frame)
        app.on_ready(on_ready)
        app.add_poll_hook(on_poll)
        app.bus.subscribe(SessionEnded, on_end)

    def _publish(self, app: SimEngineerApp, state: dict[str, Any]) -> None:
        ref = app.coach.reference
        if ref is not None and ref is not self._track_ref:
            self._track_ref = ref
            corners = [c.to_dict() for c in app.coach.corners]
            self.track = track_shape(ref, corners)
            self._track_version += 1
        self._version += 1
        self.snapshot = build_snapshot(
            app, state["tools"], state["frame"], self._version, self._track_version
        )

    # --- routes JSON
    def route(self, path: str, query: dict[str, list[str]]) -> tuple[int, Any]:
        cfg, data = self.cfg, str(self.cfg.data_dir)
        parts = [p for p in path.split("/") if p]
        if parts == ["api", "live"]:
            return 200, self.snapshot
        if parts == ["api", "track"]:
            return 200, self.track
        if parts == ["api", "meta"]:
            return 200, {"version": __version__, "language": cfg.language}
        if parts == ["api", "sessions"]:
            return 200, self.queries.run(queries.list_sessions, data)
        if parts == ["api", "refs"]:
            return 200, self.queries.run(queries.references, data)
        if parts == ["api", "profile"]:
            track = query.get("track", [""])[0]
            car = query.get("car", [""])[0]
            return 200, self.queries.run(queries.driver_profile, data, track, car, cfg.language)
        if len(parts) == 3 and parts[:2] == ["api", "sessions"] and parts[2].isdigit():
            return 200, self.queries.run(
                queries.session_detail, data, int(parts[2]), cfg.coach, cfg.language
            )
        if (
            len(parts) == 6
            and parts[:2] == ["api", "sessions"]
            and parts[3] == "laps"
            and parts[5] == "trace"
            and parts[2].isdigit()
            and parts[4].isdigit()
        ):
            return 200, self.queries.run(
                queries.lap_trace, data, int(parts[2]), int(parts[4]), cfg.coach, cfg.language
            )
        if len(parts) == 3 and parts[:2] == ["api", "setup"] and parts[2].isdigit():
            return 200, self.queries.run(
                queries.setup_view, data, int(parts[2]), cfg.setup, cfg.coach, cfg.language
            )
        return 404, {"error": "introuvable"}


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    ui: UIServer


class _Handler(BaseHTTPRequestHandler):
    server: _HTTPServer
    server_version = "SimEngineer"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), format % args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: Any) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        ui = self.server.ui
        if url.path in STATIC_FILES:
            name, ctype = STATIC_FILES[url.path]
            body = resources.files("simengineer.ui").joinpath("static", name).read_bytes()
            self._send(200, body, ctype)
            return
        if url.path == "/api/live/stream":
            self._stream(ui)
            return
        if not url.path.startswith("/api/"):
            self._json(404, {"error": "introuvable"})
            return
        try:
            code, data = ui.route(url.path, parse_qs(url.query))
        except KeyError as exc:
            code, data = 404, {"error": str(exc)}
        except Exception as exc:
            log.exception("Erreur de l'interface sur %s", url.path)
            code, data = 500, {"error": type(exc).__name__}
        self._json(code, data)

    def _stream(self, ui: UIServer) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        period = 1.0 / ui.cfg.ui.refresh_hz
        last = -1
        idle = 0.0
        try:
            while not ui.stopping:
                snap = ui.snapshot
                if snap.get("version") != last:
                    last = snap.get("version", 0)
                    payload = json.dumps(snap, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    idle = 0.0
                elif idle >= 15.0:  # maintien de connexion
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    idle = 0.0
                time.sleep(period)
                idle += period
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
