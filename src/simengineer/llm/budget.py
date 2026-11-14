"""Plafond de dépense quotidien du LLM (persisté en local)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from simengineer.core.config import LLMProviderConfig


class DailyBudget:
    def __init__(
        self, path: Path | None, cap_usd: float, today: Callable[[], date] = date.today
    ) -> None:
        self._path = path
        self.cap_usd = cap_usd
        self._today = today
        self._spent: dict[str, float] = {}
        if path is not None and path.is_file():
            try:
                self._spent = {k: float(v) for k, v in json.loads(path.read_text()).items()}
            except (ValueError, OSError):
                self._spent = {}

    @property
    def spent_today(self) -> float:
        return self._spent.get(self._today().isoformat(), 0.0)

    def allowed(self) -> bool:
        return self.cap_usd <= 0 or self.spent_today < self.cap_usd

    def add(self, provider: LLMProviderConfig, prompt_tokens: int, completion_tokens: int) -> float:
        cost = (
            prompt_tokens * provider.price_in_per_mtok
            + completion_tokens * provider.price_out_per_mtok
        ) / 1_000_000.0
        key = self._today().isoformat()
        self._spent = {key: self._spent.get(key, 0.0) + cost}  # on ne garde que le jour courant
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._spent))
        return cost
