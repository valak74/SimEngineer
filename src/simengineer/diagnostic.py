"""Diagnostic automatique de la première séance réelle.

Branché sur l'application (source `live`), il fait sans intervention :
- la liste réelle des variables du SDK et la validation de `VARS` (équivalent de dump-vars) ;
- l'enregistrement des premiers tours (variables de `VARS` uniquement) dans un `.ibt`
  rejouable, noms des autres pilotes anonymisés ;
- des contrôles automatiques : temps au tour recalculés contre ceux d'iRacing, valeurs
  observées (CarLeftRight, surfaces, drapeaux, positions…), infos de session lues ;
- la latence réelle de la voix (donnée -> son), le CPU et la RAM du collecteur ;
- un rapport lisible et un fichier zip unique sur le Bureau, à envoyer tel quel.
Rien ne sort de la machine : c'est le pilote qui décide d'envoyer le zip.
"""

from __future__ import annotations

import json
import logging
import math
import platform
import shutil
import statistics
import sys
import time
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from simengineer import __version__
from simengineer.core.events import LapCompleted, SessionEnded
from simengineer.core.log import get_logger
from simengineer.core.types import Frame, SessionInfo
from simengineer.core.vars import FAST_KEYS, SLOW_KEYS, VARS, VarsReport
from simengineer.sources.base import RawSession

if TYPE_CHECKING:
    from simengineer.app import SimEngineerApp

log = get_logger("diagnostic")

DIAG_REV = 1  # à incrémenter quand un nouveau diagnostic est nécessaire après une mise à jour
DONE_KEY = "diagnostic_done"
MAX_LAPS = 3
MAX_SECONDS = 8 * 60

_ARRAY_KEYS = {"car_idx_lap_dist_pct", "car_idx_track_surface", "car_idx_on_pit_road",
               "car_idx_position", "car_idx_f2_time"}  # fmt: skip
_INT_KEYS = {
    "lap", "gear", "player_track_surface", "session_flags", "session_state",
    "session_laps_remain", "incident_count", "car_left_right", "player_car_idx",
}  # fmt: skip
_BOOL_KEYS = {"on_pit_road", "is_on_track"}


def desktop() -> Path:
    for candidate in (Path.home() / "Desktop", Path.home() / "Bureau"):
        if candidate.is_dir():
            return candidate
    return Path.home()


def diagnostic_needed(store: Any) -> bool:
    return int(store.kv_get(DONE_KEY, 0) or 0) < DIAG_REV


class DiagnosticRecorder:
    def __init__(self, out_root: Path, zip_dir: Path | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = out_root / f"diagnostic-{stamp}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.zip_dir = zip_dir or desktop()
        self.zip_path: Path | None = None
        self._app: SimEngineerApp | None = None
        self._log_handler: logging.Handler | None = None
        self._vars: VarsReport | None = None
        self._available: set[str] = set()
        self._rows: dict[str, list[Any]] = {}
        self._slow_last: dict[str, float] = {}
        self._recording = True
        self._laps_recorded = 0
        self._t0: float | None = None
        # contrôles
        self._lap_ours: list[float] = []
        self._official: list[float] = []
        self._last_official = math.nan
        self._seen: dict[str, Counter[Any]] = {
            "car_left_right": Counter(), "player_track_surface": Counter(),
            "flags_bits": Counter(), "position": Counter(), "gear": Counter(),
        }  # fmt: skip
        self._max_speed = 0.0
        self._fuel_first = math.nan
        self._fuel_last = math.nan
        self._latlon_ok = False
        self._other_cars_max = 0
        self._latency: list[tuple[str, int, float]] = []
        self._cpu: list[float] = []
        self._rss: list[float] = []
        self._perf_last: tuple[float, float] | None = None
        self._frames = 0

    # --- branchement
    def attach(self, app: SimEngineerApp) -> None:
        self._app = app
        self._log_handler = logging.FileHandler(self.dir / "journal.txt", encoding="utf-8")
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(self._log_handler)
        app.on_ready(self._ready)
        app.add_frame_hook(self._frame)
        app.add_poll_hook(self._poll)
        app.bus.subscribe(LapCompleted, self._lap)
        app.bus.subscribe(SessionEnded, self._end)
        log.info("Diagnostic automatique actif : %s", self.dir)

    def record_latency(self, key: str, priority: int, ms: float) -> None:
        self._latency.append((key, priority, ms))

    # --- étapes
    def _ready(self) -> None:
        from simengineer.sources.dump import write_vars_doc

        assert self._app is not None
        src = self._app.source
        try:
            self._vars = write_vars_doc(src, self.dir / "irsdk_vars.md")
            self._available = {d.name for d in src.available_vars()}
        except Exception:
            log.exception("Liste des variables impossible")

    def _frame(self, f: Frame) -> None:
        self._frames += 1
        if self._t0 is None:
            self._t0 = f.session_time
        self._observe(f)
        if not self._recording:
            return
        if f.session_time - self._t0 > MAX_SECONDS:
            self._recording = False
            return
        for key in FAST_KEYS:
            value = getattr(f, key, None)
            if value is None or VARS[key].name not in self._available:
                continue
            self._rows.setdefault(key, []).append(value)
        if f.slow:
            self._slow_last.update(f.slow)
        for key in SLOW_KEYS:
            if key in self._slow_last and VARS[key].name in self._available:
                self._rows.setdefault(key, []).append(self._slow_last[key])

    def _observe(self, f: Frame) -> None:
        seen = self._seen
        seen["car_left_right"][f.car_left_right] += 1
        seen["player_track_surface"][f.player_track_surface] += 1
        seen["gear"][f.gear] += 1
        flags = f.session_flags
        while flags:
            bit = flags & -flags
            seen["flags_bits"][hex(bit)] += 1
            flags ^= bit
        if f.car_idx_position is not None and 0 <= f.player_car_idx < len(f.car_idx_position):
            seen["position"][int(f.car_idx_position[f.player_car_idx])] += 1
        self._max_speed = max(self._max_speed, f.speed * 3.6)
        if math.isfinite(f.fuel_level):
            if math.isnan(self._fuel_first):
                self._fuel_first = f.fuel_level
            self._fuel_last = f.fuel_level
        if math.isfinite(f.lat) and abs(f.lat) > 1e-6 and abs(f.lon) > 1e-6:
            self._latlon_ok = True
        if f.car_idx_lap_dist_pct is not None:
            self._other_cars_max = max(
                self._other_cars_max, int(np.count_nonzero(f.car_idx_lap_dist_pct >= 0)) - 1
            )
        if f.lap_last_time > 0 and f.lap_last_time != self._last_official:
            self._last_official = f.lap_last_time
            self._official.append(float(f.lap_last_time))

    def _lap(self, ev: LapCompleted) -> None:
        lap = ev.lap
        if lap.complete:
            self._lap_ours.append(lap.lap_time)
            if self._recording:
                self._laps_recorded += 1
                if self._laps_recorded >= MAX_LAPS:
                    self._recording = False

    def _poll(self) -> None:
        now = (time.perf_counter(), time.process_time())
        if self._perf_last is None:
            self._perf_last = now
            return
        dw, dc = now[0] - self._perf_last[0], now[1] - self._perf_last[1]
        if dw >= 10.0:
            self._perf_last = now
            self._cpu.append(100.0 * dc / dw)
            from simengineer.perf import _rss_mb

            rss = _rss_mb()
            if math.isfinite(rss):
                self._rss.append(rss)

    # --- fin
    def _end(self, _: SessionEnded) -> None:
        try:
            self.finish()
        except Exception:
            log.exception("Échec du diagnostic")

    def finish(self) -> Path | None:
        app = self._app
        assert app is not None
        if self._frames < 60:
            log.info("Diagnostic : séance trop courte, rien à analyser")
            self._close_log()
            return None
        self._write_sample(app.info)
        report = self.report(app.info)
        (self.dir / "rapport.md").write_text(report, encoding="utf-8")
        alerts = app.cfg.data_dir / app.cfg.spotter.alerts_log
        if alerts.is_file():
            shutil.copy(alerts, self.dir / "alertes.jsonl")
        (self.dir / "config.json").write_text(
            app.cfg.model_dump_json(indent=2), encoding="utf-8"
        )  # ne contient aucune clé (seulement les noms des variables d'environnement)
        self._close_log()
        self.zip_dir.mkdir(parents=True, exist_ok=True)
        self.zip_path = self.zip_dir / f"SimEngineer-diagnostic-{self.dir.name[11:]}.zip"
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for file in sorted(self.dir.iterdir()):
                z.write(file, file.name)
        app.store.kv_set(DONE_KEY, DIAG_REV)
        log.info("Diagnostic prêt : %s", self.zip_path)
        print(
            f"\n>>> Diagnostic prêt : {self.zip_path}"
            "\n>>> Envoie ce fichier dans la conversation.\n"
        )
        return self.zip_path

    def _close_log(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    def _write_sample(self, info: SessionInfo) -> None:
        from simengineer.sources.ibt_format import write_ibt

        n = min((len(v) for v in self._rows.values()), default=0)
        if n < 60:
            return
        cols: dict[str, np.ndarray[Any, Any]] = {}
        for key, values in self._rows.items():
            name = VARS[key].name
            if key in _ARRAY_KEYS:
                arr = np.stack([np.asarray(v) for v in values[:n]])
                cols[name] = arr.astype(np.float32 if arr.dtype.kind == "f" else arr.dtype)
            elif key in _BOOL_KEYS:
                cols[name] = np.asarray(values[:n], dtype=np.bool_)
            elif key in _INT_KEYS:
                cols[name] = np.asarray(values[:n], dtype=np.int64)
            else:
                cols[name] = np.asarray(values[:n], dtype=np.float64)
        anon = replace(info, drivers={i: f"Pilote {i}" for i in info.drivers})
        write_ibt(RawSession(anon, cols), self.dir / "echantillon.ibt")

    # --- rapport
    def checks(self, info: SessionInfo) -> list[tuple[str, str, str]]:
        """(statut OK/ATTENTION/INFO, contrôle, détail)."""
        out: list[tuple[str, str, str]] = []
        v = self._vars
        if v is None:
            out.append(("ATTENTION", "Variables du SDK", "liste indisponible"))
        else:
            status = "OK" if v.ok and not v.missing_optional else "ATTENTION"
            detail = "toutes présentes"
            if v.missing_required or v.missing_optional:
                detail = "absentes : " + ", ".join(v.missing_required + v.missing_optional)
            out.append((status, "Variables de VARS", detail))
        out.append(
            ("OK" if info.track_length_m > 0 else "ATTENTION", "Infos de session",
             f"{info.track_name}, {info.track_length_m:.0f} m, {info.car_name}, "
             f"{info.session_type}, tours={info.session_laps}, temps={info.session_time_s}, "
             f"réservoir={info.fuel_capacity_l} L, incidents={info.incident_limit}, "
             f"stands={info.pit_speed_limit_kmh} km/h")
        )  # fmt: skip
        diffs = []
        for ours in self._lap_ours:
            near = [abs(o - ours) for o in self._official]
            if near:
                diffs.append(min(near))
        if diffs:
            worst = max(diffs)
            out.append(
                ("OK" if worst < 0.05 else "ATTENTION", "Temps au tour recalculés vs iRacing",
                 f"{len(diffs)} tours, écart max {worst * 1000:.0f} ms")
            )  # fmt: skip
        else:
            out.append(("INFO", "Temps au tour", "aucun tour complet comparé"))
        clr = dict(self._seen["car_left_right"])
        out.append(
            ("OK" if set(clr) - {0, 1} else "INFO", "CarLeftRight observé",
             json.dumps({str(k): c for k, c in sorted(clr.items())}))
        )  # fmt: skip
        surfaces = {str(k): c for k, c in sorted(self._seen["player_track_surface"].items())}
        out.append(("INFO", "Surfaces du pilote observées", json.dumps(surfaces)))
        out.append(
            ("INFO", "Bits de drapeaux observés", json.dumps(dict(self._seen["flags_bits"])))
        )
        pos = self._seen["position"]
        out.append(("OK" if pos else "INFO", "Positions du pilote",
                    json.dumps({str(k): c for k, c in sorted(pos.items())})))  # fmt: skip
        out.append(("INFO", "Autres voitures en piste (max)", str(self._other_cars_max)))
        speed_ok = 40 < self._max_speed < 400
        out.append(
            ("OK" if speed_ok else "ATTENTION", "Vitesse max", f"{self._max_speed:.0f} km/h")
        )
        fuel_ok = math.isfinite(self._fuel_first) and self._fuel_last <= self._fuel_first
        out.append(("OK" if fuel_ok else "ATTENTION", "Carburant",
                    f"{self._fuel_first:.2f} L -> {self._fuel_last:.2f} L"))  # fmt: skip
        out.append(("OK" if self._latlon_ok else "ATTENTION", "Lat/Lon en direct",
                    "présentes" if self._latlon_ok else "absentes ou nulles"))  # fmt: skip
        p0 = [ms for _, p, ms in self._latency if p == 0]
        if p0:
            p0.sort()
            p95 = p0[min(len(p0) - 1, int(0.95 * len(p0)))]
            med = statistics.median(p0)
            detail = f"médiane {med:.0f} ms, 95 % {p95:.0f} ms, {len(p0)} alertes"
            out.append(("OK" if p95 < 200 else "ATTENTION", "Latence voix P0", detail))
        else:
            out.append(("INFO", "Latence voix", "aucune alerte P0 pendant la séance"))
        if self._cpu:
            cpu = statistics.median(self._cpu)
            out.append(("OK" if cpu < 3.0 else "ATTENTION", "CPU du collecteur",
                        f"médiane {cpu:.2f} % d'un cœur, max {max(self._cpu):.2f} %"))  # fmt: skip
        if self._rss:
            out.append(("OK" if max(self._rss) < 150 else "ATTENTION", "RAM du collecteur",
                        f"max {max(self._rss):.0f} Mo"))  # fmt: skip
        out.append(("INFO", "Échantillon enregistré", f"{self._laps_recorded} tour(s) complet(s)"))
        return out

    def report(self, info: SessionInfo) -> str:
        lines = [
            "# Diagnostic SimEngineer",
            "",
            f"- Version : {__version__} (diagnostic n°{DIAG_REV})",
            f"- Date : {datetime.now():%Y-%m-%d %H:%M}",
            f"- Machine : {platform.system()} {platform.release()},"
            f" Python {sys.version.split()[0]}",
            f"- Images traitées : {self._frames}",
            "",
            "| Statut | Contrôle | Détail |",
            "|---|---|---|",
        ]
        lines += [f"| {s} | {c} | {d} |" for s, c, d in self.checks(info)]
        lines += ["", "Fichiers : irsdk_vars.md, echantillon.ibt (rejouable), alertes.jsonl,",
                  "journal.txt, config.json (sans clé d'API).", ""]  # fmt: skip
        return "\n".join(lines)
