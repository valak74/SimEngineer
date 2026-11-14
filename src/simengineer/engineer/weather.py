"""Surveillance météo/piste (variables lues à 1 Hz)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WeatherStatus:
    track_temp_c: float | None = None
    air_temp_c: float | None = None
    track_temp_delta_c: float | None = None  # depuis le début de la session
    wetness: int | None = None


class WeatherMonitor:
    def __init__(self, temp_delta_c: float = 3.0) -> None:
        self._delta = temp_delta_c
        self._start: float | None = None
        self._announced: float | None = None
        self._wet: int | None = None
        self._status = WeatherStatus()

    def on_slow(self, slow: dict[str, float]) -> list[tuple[str, dict[str, Any]]]:
        """Met à jour l'état ; renvoie les annonces (clé de gabarit, paramètres)."""
        out: list[tuple[str, dict[str, Any]]] = []
        temp = slow.get("track_temp")
        air = slow.get("air_temp")
        wet_raw = slow.get("track_wetness")
        wet = int(wet_raw) if wet_raw is not None and wet_raw == wet_raw else None
        delta = None
        if temp is not None and temp == temp:
            if self._start is None:
                self._start = self._announced = temp
            delta = temp - self._start
            if self._announced is not None and abs(temp - self._announced) >= self._delta:
                self._announced = temp
                out.append(("weather_track_temp", {"temp": round(temp), "delta": round(delta)}))
        if wet is not None:
            if self._wet is not None and wet != self._wet:
                out.append(("weather_wet" if wet > self._wet else "weather_drying", {}))
            self._wet = wet
        self._status = WeatherStatus(temp, air, delta, wet)
        return out

    def status(self) -> WeatherStatus:
        return self._status
