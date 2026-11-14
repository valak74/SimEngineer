"""Dialogue avec l'ingénieur : question -> (LLM + outils) -> réponse courte.

Garanties :
- appel d'outil inconnu ou mal formé : une relance, puis réponse par gabarit ;
- tout chiffre de la réponse doit figurer dans une sortie d'outil (ou la question) :
  sinon une relance, puis gabarit ;
- LLM indisponible (délai, panne, budget, pas de clé) ou désactivé : gabarit local.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from simengineer.core.config import LLMConfig
from simengineer.core.log import get_logger
from simengineer.llm.client import LLMUnavailableError, Message, ProviderChain
from simengineer.llm.prompts import RETRY_NUMBERS, system_prompt
from simengineer.llm.tools import ToolOutcome, ToolRegistry, ungrounded_numbers
from simengineer.voice.templates import render

log = get_logger("dialogue")

MAX_ANSWER_WORDS = 30  # le prompt demande < 15 ; au-delà de 30, on préfère le gabarit


@dataclass(slots=True)
class DialogueResult:
    text: str
    source: str  # "llm:<fournisseur>" ou "local" ou "local:<raison>"
    tools_used: list[str] = field(default_factory=list)


# Intentions du mode local : (motif, outil). L'ordre compte.
_INTENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(fen[êe]tre|stand|arr[êe]t|pit|box|ravitaill)", re.I), "get_pit_window"),
    (re.compile(r"\b(carbu|essence|fuel|litre|liter|gas\b)", re.I), "get_fuel_status"),
    (re.compile(r"\b(pneu|gomme|tyre|tire)", re.I), "get_tyres"),
    (re.compile(r"\b([ée]cart|devant|derri[èe]re|gap|ahead|behind)", re.I), "get_gap"),
    (re.compile(r"\b(m[ée]t[ée]o|temp[ée]rature|piste|weather|track temp)", re.I), "get_weather"),
    (
        re.compile(
            r"\b(conseil|virage|am[ée]lior|perd|gagn|coach|advice|corner|turn|faster)", re.I
        ),
        "get_corner_advice",
    ),
    (re.compile(r"\b(tour|chrono|temps|lap|time|delta)", re.I), "get_lap_summary"),
]
_FUEL_CMD = re.compile(
    r"\b(mets?|ajoute|r[èe]gle|add|set|put)\b.*?(\d+(?:[.,]\d+)?)\s*(l\b|litres?|liters?)", re.I
)
_TYRES_OFF = re.compile(
    r"\b(pas de pneus?|sans pneus?|garde les pneus?|no tyres?|no tires?|keep (the )?tyres?)", re.I
)
_TYRES_ON = re.compile(r"\b(change[rz]?|chang(e|ing)|new|neufs?)\b.*\b(pneus?|tyres?|tires?)", re.I)
_CORNER = re.compile(r"\b(?:virage|turn|corner|t)\s*0?(\d{1,2})\b", re.I)


class DialogueEngine:
    def __init__(
        self,
        tools: ToolRegistry,
        chain: ProviderChain | None,
        cfg: LLMConfig,
        lang: str = "fr",
    ) -> None:
        self.tools = tools
        self.chain = chain
        self.cfg = cfg
        self.lang = lang
        self._system: Message = {"role": "system", "content": system_prompt(lang)}

    # --- mode local (gabarits)
    def local_answer(self, question: str, reason: str = "") -> DialogueResult:
        source = f"local:{reason}" if reason else "local"
        m = _FUEL_CMD.search(question)
        if m:
            liters = float(m.group(2).replace(",", "."))
            out = self.tools.execute("set_pit_fuel", {"liters": liters})
            return DialogueResult(out.say or self._sorry(), source, ["set_pit_fuel"])
        if _TYRES_OFF.search(question) or _TYRES_ON.search(question):
            change = not _TYRES_OFF.search(question)
            out = self.tools.execute("set_pit_tyres", {"change": change})
            return DialogueResult(out.say, source, ["set_pit_tyres"])
        for pattern, tool in _INTENTS:
            if pattern.search(question):
                args: dict[str, int] = {}
                corner = _CORNER.search(question)
                if tool == "get_corner_advice" and corner:
                    args["corner"] = int(corner.group(1))
                out = self.tools.execute(tool, args)
                return DialogueResult(out.say or self._sorry(), source, [tool])
        return DialogueResult(self._sorry(), source)

    def _sorry(self) -> str:
        return render("dlg_not_understood", self.lang)

    # --- avec LLM
    def answer(self, question: str) -> DialogueResult:
        question = question.strip()
        if not question:
            return DialogueResult(self._sorry(), "local:vide")
        if self.chain is None or not self.cfg.enabled:
            return self.local_answer(question)
        try:
            return self._llm_answer(question)
        except LLMUnavailableError as exc:
            log.warning("LLM indisponible, réponse par gabarit : %s", exc)
            return self.local_answer(question, "llm_indisponible")

    def _fallback(self, question: str, outcomes: list[ToolOutcome], reason: str) -> DialogueResult:
        """Gabarit : phrase du dernier outil réussi, sinon intention locale."""
        log.warning("Réponse LLM écartée (%s), gabarit utilisé", reason)
        good = [o for o in outcomes if o.ok and o.say]
        if good:
            return DialogueResult(good[-1].say, f"local:{reason}")
        return self.local_answer(question, reason)

    def _llm_answer(self, question: str) -> DialogueResult:
        assert self.chain is not None
        # Préfixe stable : prompt système + définitions d'outils identiques à chaque requête.
        messages: list[Message] = [self._system, {"role": "user", "content": question}]
        outcomes: list[ToolOutcome] = []
        used: list[str] = []
        invalid_calls = 0
        number_retries = 0
        for _ in range(self.cfg.max_tool_rounds + 2):
            res = self.chain.complete(messages, self.tools.definitions)
            if res.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": res.content,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": c.arguments},
                            }
                            for c in res.tool_calls
                        ],
                    }
                )
                for call in res.tool_calls:
                    out = self.tools.execute(call.name, call.arguments)
                    if out.ok:
                        outcomes.append(out)
                        used.append(call.name)
                    else:
                        invalid_calls += 1
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": out.content}
                    )
                if invalid_calls > 1:  # une seule relance pour un appel mal formé
                    return self._fallback(question, outcomes, "appel_outil_invalide")
                continue
            text = (res.content or "").strip()
            if not text:
                return self._fallback(question, outcomes, "réponse_vide")
            sources = [o.content for o in outcomes] + [question]
            bad = ungrounded_numbers(text, sources)
            if bad or len(text.split()) > MAX_ANSWER_WORDS:
                if number_retries >= 1:
                    return self._fallback(question, outcomes, "chiffre_non_sourcé")
                number_retries += 1
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": RETRY_NUMBERS[self.lang]})
                continue
            return DialogueResult(text, f"llm:{self.chain.last_provider}", used)
        return self._fallback(question, outcomes, "trop_d_allers_retours")
