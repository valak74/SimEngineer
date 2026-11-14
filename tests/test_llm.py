from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig, LLMConfig
from simengineer.engineer.pit_commands import DisabledPitCommander, RecordingPitCommander
from simengineer.llm.budget import DailyBudget
from simengineer.llm.client import (
    ChatResult,
    LLMUnavailableError,
    Message,
    Provider,
    ProviderChain,
    ToolCall,
    build_chain,
)
from simengineer.llm.dialogue import DialogueEngine
from simengineer.llm.prompts import system_prompt
from simengineer.llm.report import narrative_from_chain
from simengineer.llm.service import DialogueService
from simengineer.llm.tools import ToolRegistry, numbers_in, ungrounded_numbers
from simengineer.runtime import make_dialogue_engine
from simengineer.sources.scenarios import get_scenario
from simengineer.sources.synthetic import SyntheticSource
from simengineer.voice.arbiter import Priority
from simengineer.voice.ptt import PushToTalk
from simengineer.voice.stt import NullSTT

Step = ChatResult | Exception | Callable[[list[Message]], ChatResult]


class FakeClient:
    """Faux client LLM : rejoue un script de réponses et enregistre les requêtes."""

    def __init__(self, *script: Step) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        timeout_s: float,
        max_tokens: int,
    ) -> ChatResult:
        self.calls.append(
            {"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools,
             "timeout": timeout_s}
        )  # fmt: skip
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages)
        return step


def call(name: str, args: str = "{}", cid: str = "c1") -> ChatResult:
    return ChatResult(None, [ToolCall(cid, name, args)], 900, 20)


def text(content: str) -> ChatResult:
    return ChatResult(content, [], 950, 15)


def last_tool(messages: list[Message]) -> dict[str, Any]:
    return json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def race_app(tmp_path_factory: pytest.TempPathFactory) -> SimEngineerApp:
    cfg = AppConfig()
    cfg.data_dir = tmp_path_factory.mktemp("llm")
    cfg.voice.tts_backend = "null"
    cfg.source.realtime = False
    app = SimEngineerApp(cfg, SyntheticSource(get_scenario("race")))
    app.run(max_frames=int(8.5 * 92.9 * 60))
    return app


def engine(
    app: SimEngineerApp, *clients: FakeClient, cfg: LLMConfig | None = None, cap: float = 1.0
) -> tuple[DialogueEngine, ProviderChain]:
    llm = cfg or app.cfg.llm
    providers = [Provider(p, c) for p, c in zip(llm.providers, clients, strict=False)]
    chain = ProviderChain(providers, llm, DailyBudget(None, cap))
    assert app.engineer is not None
    tools = ToolRegistry(app.engineer, app.coach, "fr")
    return DialogueEngine(tools, chain, llm, "fr"), chain


# --- outils
def test_tools_read_precomputed_state(race_app: SimEngineerApp) -> None:
    eng, _ = engine(race_app)
    fuel = eng.tools.execute("get_fuel_status", "{}")
    assert fuel.ok and fuel.data["avg_per_lap_l"] == 2.5 and fuel.data["can_finish"] is False
    assert fuel.say == f"Il manque {fuel.data['fuel_to_add_l']} litres pour finir"
    pit = eng.tools.execute("get_pit_window", "")
    assert pit.data["close_lap"] == 10 and pit.say.startswith("Fenêtre de stand : tours ")
    laps = eng.tools.execute("get_lap_summary", "{}")
    assert laps.data["best_lap_s"] < laps.data["last_lap_s"] and "meilleur" in laps.say
    adv = eng.tools.execute("get_corner_advice", "{}")
    assert adv.data["advice"] and adv.say.startswith("Virage ")
    one = eng.tools.execute("get_corner_advice", '{"corner": 40}')
    assert one.ok and one.data["advice"] == [] and one.say == "Pas de conseil pour l'instant"
    assert eng.tools.execute("get_gap", "{}").data["ahead_s"] is not None
    assert eng.tools.execute("get_tyres", "{}").data["stint_laps"] == 8
    assert eng.tools.execute("get_weather", "{}").say.startswith("Piste ")
    assert eng.tools.names[-2:] == ["set_pit_fuel", "set_pit_tyres"]


def test_tool_validation_is_strict(race_app: SimEngineerApp) -> None:
    tools = engine(race_app)[0].tools
    for name, args in [
        ("get_secret", "{}"),
        ("get_fuel_status", '{"extra": 1}'),
        ("get_fuel_status", "pas du json"),
        ("get_fuel_status", "[1]"),
        ("set_pit_fuel", '{"liters": "beaucoup"}'),
        ("set_pit_fuel", '{"liters": 5000}'),
        ("set_pit_fuel", "{}"),
        ("set_pit_tyres", '{"change": "oui"}'),
        ("get_corner_advice", '{"corner": 0}'),
    ]:
        out = tools.execute(name, args)
        assert not out.ok and "error" in out.data, (name, args)
    assert tools.execute("set_pit_fuel", '{"liters": 20}').say.startswith("Commandes de stand dés")


def test_number_grounding_helpers() -> None:
    assert numbers_in("9,30 tours, T03, 1:32,5 et 12.0") == {"9.3", "3", "1", "32.5", "12"}
    assert ungrounded_numbers("Carburant pour 9,3 tours", ['{"laps_in_tank": 9.3}']) == set()
    assert ungrounded_numbers("Environ 9 tours", ['{"laps_in_tank": 9.3}']) == {"9"}


# --- dialogue avec faux client
def test_answer_uses_exact_tool_numbers(race_app: SimEngineerApp) -> None:
    def final(messages: list[Message]) -> ChatResult:
        data = last_tool(messages)
        laps = str(data["laps_in_tank"]).replace(".", ",")
        return text(
            f"Tu as du carburant pour {laps} tours, il manque {data['fuel_to_add_l']} litres."
        )

    client = FakeClient(call("get_fuel_status"), final)
    eng, chain = engine(race_app, client)
    res = eng.answer("Combien de carburant il me reste ?")
    truth = eng.tools.execute("get_fuel_status", "{}").data
    assert res.source == "llm:qwen" and res.tools_used == ["get_fuel_status"]
    assert numbers_in(res.text) == numbers_in(f"{truth['laps_in_tank']} {truth['fuel_to_add_l']}")
    # Requête : délai de 8 s, préfixe stable (système + outils), sortie d'outil transmise
    assert all(c["timeout"] == 8.0 and c["model"] == "qwen-flash" for c in client.calls)
    assert client.calls[0]["messages"][0] == {"role": "system", "content": system_prompt("fr")}
    assert client.calls[0]["tools"] == client.calls[1]["tools"] == eng.tools.definitions
    assert client.calls[1]["messages"][-1]["role"] == "tool"
    assert chain.budget.spent_today > 0


def test_prompt_prefix_is_static_and_private(race_app: SimEngineerApp) -> None:
    c1, c2 = FakeClient(text("Bien reçu.")), FakeClient(text("Bien reçu."))
    engine(race_app, c1)[0].answer("Salut")
    engine(race_app, c2)[0].answer("Autre question")
    assert c1.calls[0]["messages"][0] == c2.calls[0]["messages"][0]
    assert json.dumps(c1.calls[0]["tools"]) == json.dumps(c2.calls[0]["tools"])
    client = FakeClient(call("get_gap"), call("get_lap_summary", cid="c2"), text("Bien reçu."))
    engine(race_app, client)[0].answer("Qui est devant ?")
    sent = json.dumps(client.calls, ensure_ascii=False)
    for name in race_app.info.drivers.values():
        assert name not in sent  # aucun nom de pilote ne part vers le LLM
    assert "npz" not in sent and "session_time" not in sent


def test_invented_number_is_rejected_then_template(race_app: SimEngineerApp) -> None:
    client = FakeClient(call("get_fuel_status"), text("Il te reste 42 tours."), text("Environ 41."))
    eng, _ = engine(race_app, client)
    res = eng.answer("Carburant ?")
    assert res.source == "local:chiffre_non_sourcé"
    assert res.text == eng.tools.execute("get_fuel_status", "{}").say
    assert "exactement les chiffres" in client.calls[2]["messages"][-1]["content"]


def test_invented_number_retry_can_succeed(race_app: SimEngineerApp) -> None:
    client = FakeClient(call("get_pit_window"), text("Rentre au tour 99."), text("Stand ce tour."))
    res = engine(race_app, client)[0].answer("Je rentre quand ?")
    assert res.text == "Stand ce tour." and res.source == "llm:qwen"


def test_malformed_tool_call_one_retry_then_template(race_app: SimEngineerApp) -> None:
    ok = FakeClient(call("get_fuel"), call("get_fuel_status", cid="c2"), text("Pas d'arrêt."))
    res = engine(race_app, ok)[0].answer("Carburant ?")
    assert res.source == "llm:qwen" and res.text == "Pas d'arrêt."
    assert "outil inconnu" in ok.calls[1]["messages"][-1]["content"]
    bad = FakeClient(call("get_fuel"), call("get_fuel_status", '{"x": 1}', "c2"), text("jamais lu"))
    eng, _ = engine(race_app, bad)
    res = eng.answer("Combien de carburant ?")
    assert res.source == "local:appel_outil_invalide" and len(bad.calls) == 2
    assert res.text == eng.tools.execute("get_fuel_status", "{}").say


def test_timeout_retry_then_provider_failover(race_app: SimEngineerApp) -> None:
    primary = FakeClient(TimeoutError(), TimeoutError())
    backup = FakeClient(text("Bien reçu."))
    eng, chain = engine(race_app, primary, backup)
    res = eng.answer("Tu me reçois ?")
    assert res.source == "llm:deepseek" and res.text == "Bien reçu."
    assert len(primary.calls) == 2 and len(backup.calls) == 1  # 1 tentative + 1 relance
    assert backup.calls[0]["model"] == "deepseek-v4-flash"
    assert chain.last_provider == "deepseek"


def test_all_providers_down_falls_back_to_template(race_app: SimEngineerApp) -> None:
    primary = FakeClient(TimeoutError(), ConnectionError())
    backup = FakeClient(RuntimeError("503"))
    eng, _ = engine(race_app, primary, backup)
    res = eng.answer("Mes pneus ?")
    assert res.source == "local:llm_indisponible"
    assert res.text == eng.tools.execute("get_tyres", "{}").say


def test_daily_budget_cap(race_app: SimEngineerApp, tmp_path: Path) -> None:
    day = [date(2026, 1, 1)]
    budget = DailyBudget(tmp_path / "b.json", 0.001, lambda: day[0])
    p = race_app.cfg.llm.providers[0]
    assert budget.add(p, 1_000_000, 0) == pytest.approx(p.price_in_per_mtok)
    assert (
        not budget.allowed()
        and not DailyBudget(tmp_path / "b.json", 0.001, lambda: day[0]).allowed()
    )
    client = FakeClient(text("jamais appelé"))
    chain = ProviderChain([Provider(p, client)], race_app.cfg.llm, budget)
    with pytest.raises(LLMUnavailableError):
        chain.complete([{"role": "user", "content": "x"}])
    assert client.calls == []
    day[0] = date(2026, 1, 2)  # nouveau jour : compteur remis à zéro
    assert budget.allowed() and budget.spent_today == 0.0


def test_no_key_means_local_mode(race_app: SimEngineerApp) -> None:
    assert build_chain(race_app.cfg.llm, DailyBudget(None, 1.0), env={}) is None
    off = LLMConfig(enabled=False)
    assert build_chain(off, DailyBudget(None, 1.0), env={"DASHSCOPE_API_KEY": "k"}) is None
    eng = make_dialogue_engine(race_app, use_env=False)
    assert eng.chain is None and eng.answer("carburant ?").source == "local"


# --- mode « tout local »
@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("Combien de carburant ?", "get_fuel_status"),
        ("C'est quand ma fenêtre de stand ?", "get_pit_window"),
        ("Mon dernier tour ?", "get_lap_summary"),
        ("Où est-ce que je perds du temps ?", "get_corner_advice"),
        ("Un conseil pour le virage 3 ?", "get_corner_advice"),
        ("L'écart avec celui de devant ?", "get_gap"),
        ("Comment vont les pneus ?", "get_tyres"),
        ("Température de la piste ?", "get_weather"),
        ("How much fuel do I have?", "get_fuel_status"),
    ],
)
def test_local_mode_intents(race_app: SimEngineerApp, question: str, tool: str) -> None:
    eng = make_dialogue_engine(race_app, use_env=False)
    res = eng.answer(question)
    assert res.source == "local" and res.tools_used == [tool]
    args = {"corner": 3} if "virage 3" in question else {}
    assert res.text == eng.tools.execute(tool, args).say


def test_local_mode_pit_commands_and_unknown(race_app: SimEngineerApp) -> None:
    eng = make_dialogue_engine(race_app, use_env=False)
    assert eng.answer("Mets 20 litres au stand").text.startswith("Commandes de stand désactivées")
    assert eng.answer("Raconte-moi une blague").text == "Je n'ai pas compris, répète s'il te plaît"
    assert eng.answer("   ").source == "local:vide"
    assert race_app.engineer is not None
    rec = RecordingPitCommander()
    race_app.engineer.commander = rec
    try:
        assert eng.answer("Ajoute 17,5 litres").text == "Stand réglé : 18 litres"
        assert eng.answer("Pas de pneus au prochain arrêt").text == "Stand réglé : pas de pneus"
        assert eng.answer("Change les pneus").text == "Stand réglé : changement de pneus"
        assert rec.commands == [("fuel", 18.0), ("tyres", 0.0), ("tyres", 1.0)]
    finally:
        race_app.engineer.commander = DisabledPitCommander()


# --- rapport
def test_report_narrative_grounding(race_app: SimEngineerApp) -> None:
    summary = {"best_lap_s": 92.1, "laps_valid": 7, "top_corners": [{"corner": "T03"}]}
    good = FakeClient(text("Meilleur tour en 92,1 s sur 7 tours valides ; travaille le T03."))
    chain = ProviderChain(
        [Provider(race_app.cfg.llm.providers[0], good)], race_app.cfg.llm, DailyBudget(None, 1.0)
    )
    assert narrative_from_chain(chain, summary, "fr").startswith("Meilleur tour en 92,1")
    assert good.calls[0]["model"] == "qwen-plus" and good.calls[0]["tools"] is None
    assert json.loads(good.calls[0]["messages"][1]["content"]) == summary
    bad = FakeClient(text("Tu peux gagner 1,5 seconde."))
    chain = ProviderChain(
        [Provider(race_app.cfg.llm.providers[0], bad)], race_app.cfg.llm, DailyBudget(None, 1.0)
    )
    assert narrative_from_chain(chain, summary, "fr") == ""
    down = ProviderChain([], race_app.cfg.llm, DailyBudget(None, 1.0))
    assert narrative_from_chain(down, summary, "fr") == ""


# --- push-to-talk et service
class FakeButton:
    def __init__(self) -> None:
        self.down = False

    def pressed(self) -> bool:
        return self.down


class FakeRecorder:
    def __init__(self) -> None:
        self.recording = False

    def start(self) -> None:
        self.recording = True

    def stop(self) -> np.ndarray[Any, Any]:
        self.recording = False
        return np.zeros(16000, dtype=np.int16)


def test_push_to_talk_to_spoken_answer(race_app: SimEngineerApp) -> None:
    service = DialogueService(make_dialogue_engine(race_app, use_env=False))
    button, recorder = FakeButton(), FakeRecorder()
    ptt = PushToTalk(button, recorder, NullSTT("combien de carburant"), service.ask, poll_s=0.002)
    ptt.start()
    assert not recorder.recording  # le micro n'est ouvert que pendant l'appui
    button.down = True
    time.sleep(0.05)
    assert recorder.recording
    button.down = False
    utts = []
    deadline = time.monotonic() + 3.0
    while not utts and time.monotonic() < deadline:
        utts = service.poll()
        time.sleep(0.01)
    ptt.close()
    service.close()
    assert len(utts) == 1 and utts[0].priority is Priority.P3
    assert utts[0].text.startswith("Il manque ")
