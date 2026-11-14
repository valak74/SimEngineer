"""Commentaire de rapport rédigé par le LLM à partir du seul résumé structuré."""

from __future__ import annotations

import json
from typing import Any

from simengineer.core.config import AppConfig
from simengineer.core.log import get_logger
from simengineer.llm.budget import DailyBudget
from simengineer.llm.client import LLMUnavailableError, ProviderChain, build_chain
from simengineer.llm.prompts import report_prompt
from simengineer.llm.tools import ungrounded_numbers

log = get_logger("llm.report")


def narrative_from_chain(chain: ProviderChain, summary: dict[str, Any], lang: str) -> str:
    """Commentaire du LLM, ou "" s'il est indisponible ou cite un chiffre absent du résumé."""
    payload = json.dumps(summary, ensure_ascii=False)
    messages = [
        {"role": "system", "content": report_prompt(lang)},
        {"role": "user", "content": payload},
    ]
    try:
        res = chain.complete(messages, None, report=True)
    except LLMUnavailableError as exc:
        log.warning("Commentaire LLM indisponible : %s", exc)
        return ""
    text = (res.content or "").strip()
    bad = ungrounded_numbers(text, [payload])
    if bad:
        log.warning("Commentaire LLM écarté : chiffres non sourcés %s", sorted(bad))
        return ""
    return text


def llm_narrative(cfg: AppConfig, summary: dict[str, Any]) -> str:
    budget = DailyBudget(cfg.data_dir / "llm_budget.json", cfg.llm.daily_budget_usd)
    chain = build_chain(cfg.llm, budget)
    if chain is None:
        log.info("Mode local : rapport sans commentaire LLM")
        return ""
    return narrative_from_chain(chain, summary, cfg.language)
