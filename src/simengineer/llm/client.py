"""Client LLM compatible OpenAI, multi-fournisseurs, avec bascule automatique.

Politique : délai max par appel (8 s par défaut), une seule nouvelle tentative sur le
fournisseur principal, puis bascule sur le fournisseur de secours ; si tout échoue,
`LLMUnavailableError` et l'appelant répond par gabarit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from simengineer.core.config import LLMConfig, LLMProviderConfig, api_key_for
from simengineer.core.log import get_logger
from simengineer.llm.budget import DailyBudget

log = get_logger("llm")

Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON brut, validé plus tard par pydantic


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMUnavailableError(RuntimeError):
    """Aucun fournisseur n'a répondu (ou LLM désactivé / budget atteint / pas de clé)."""


class ChatClient(Protocol):
    def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        timeout_s: float,
        max_tokens: int,
    ) -> ChatResult: ...


class OpenAIChatClient:
    """Enveloppe du SDK `openai` (import paresseux) pour un endpoint compatible."""

    def __init__(self, base_url: str, api_key: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        timeout_s: float,
        max_tokens: int,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": timeout_s,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        usage = resp.usage
        return ChatResult(
            msg.content,
            calls,
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )


@dataclass(slots=True)
class Provider:
    cfg: LLMProviderConfig
    client: ChatClient


class ProviderChain:
    def __init__(self, providers: list[Provider], cfg: LLMConfig, budget: DailyBudget) -> None:
        self.providers = providers
        self.cfg = cfg
        self.budget = budget
        self.last_provider = ""

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        report: bool = False,
    ) -> ChatResult:
        if not self.providers:
            raise LLMUnavailableError("aucun fournisseur configuré avec une clé d'API")
        if not self.budget.allowed():
            raise LLMUnavailableError(
                f"plafond quotidien atteint ({self.budget.spent_today:.4f} USD)"
            )
        max_tokens = self.cfg.report_max_tokens if report else self.cfg.max_tokens
        timeout = self.cfg.timeout_s * (3 if report else 1)
        errors: list[str] = []
        for rank, provider in enumerate(self.providers):
            model = (
                (provider.cfg.report_model or provider.cfg.model) if report else provider.cfg.model
            )
            attempts = 1 + (self.cfg.max_retries if rank == 0 else 0)
            for attempt in range(attempts):
                try:
                    res = provider.client.complete(model, messages, tools, timeout, max_tokens)
                except Exception as exc:  # délai dépassé, réseau, HTTP 4xx/5xx…
                    errors.append(f"{provider.cfg.name}#{attempt + 1}: {type(exc).__name__}")
                    log.warning("LLM %s en échec (%s)", provider.cfg.name, type(exc).__name__)
                    continue
                self.budget.add(provider.cfg, res.prompt_tokens, res.completion_tokens)
                self.last_provider = provider.cfg.name
                return res
        raise LLMUnavailableError("tous les fournisseurs ont échoué : " + ", ".join(errors))


def build_chain(
    cfg: LLMConfig, budget: DailyBudget, env: Mapping[str, str] | None = None
) -> ProviderChain | None:
    """Chaîne des fournisseurs qui ont une clé dans l'environnement ; None en mode local."""
    if not cfg.enabled:
        return None
    providers = []
    for p in cfg.providers:
        key = api_key_for(p, env)
        if key is None:
            log.info("LLM %s ignoré : variable %s absente", p.name, p.api_key_env)
            continue
        try:
            providers.append(Provider(p, OpenAIChatClient(p.base_url, key)))
        except ImportError:
            log.warning("SDK openai absent : pip install simengineer[llm]")
            return None
    return ProviderChain(providers, cfg, budget) if providers else None
