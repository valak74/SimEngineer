"""Assemblage de l'application à partir de la configuration."""

from __future__ import annotations

from collections.abc import Callable

from simengineer.app import SimEngineerApp
from simengineer.core.config import AppConfig
from simengineer.core.log import get_logger
from simengineer.llm.budget import DailyBudget
from simengineer.llm.client import ProviderChain, build_chain
from simengineer.llm.dialogue import DialogueEngine
from simengineer.llm.service import DialogueService
from simengineer.llm.tools import ToolRegistry
from simengineer.sources.factory import make_source
from simengineer.voice.arbiter import Utterance
from simengineer.voice.factory import make_speaker

log = get_logger("runtime")


def _log_latency(utt: Utterance, ms: float) -> None:
    if utt.priority == 0:
        level = log.warning if ms > 200.0 else log.debug
        level("Alerte %r : donnée -> son en %.0f ms", utt.key, ms)


def make_dialogue_engine(
    app: SimEngineerApp, chain: ProviderChain | None = None, use_env: bool = True
) -> DialogueEngine:
    """Moteur de dialogue branché sur l'état de l'application (à appeler après `_setup`)."""
    cfg = app.cfg
    assert app.engineer is not None
    if chain is None and use_env:
        budget = DailyBudget(cfg.data_dir / "llm_budget.json", cfg.llm.daily_budget_usd)
        chain = build_chain(cfg.llm, budget)
    tools = ToolRegistry(app.engineer, app.coach, cfg.language)
    return DialogueEngine(tools, chain, cfg.llm, cfg.language)


def attach_dialogue(app: SimEngineerApp) -> None:
    """Dialogue vocal push-to-talk : démarré avec la session, si un bouton est configuré."""
    cfg = app.cfg
    if cfg.voice.ptt_kind == "none" or cfg.voice.stt_backend == "none":
        return

    def start() -> None:
        from simengineer.voice.ptt import MicRecorder, PushToTalk, make_button
        from simengineer.voice.stt import FasterWhisperSTT

        try:
            button = make_button(cfg.voice)
            if button is None:
                return
            service = DialogueService(make_dialogue_engine(app))
            stt = FasterWhisperSTT(cfg.voice.stt_model, cfg.voice.stt_compute_type)
            ptt = PushToTalk(
                button, MicRecorder(cfg.voice.input_device), stt, service.ask, cfg.language,
                cfg.voice.max_record_s,
            )  # fmt: skip
            ptt.start()
        except Exception as exc:  # micro/bouton absents : l'appli continue sans dialogue
            log.warning("Dialogue vocal désactivé : %s", exc)
            return

        def poll() -> None:
            for utt in service.poll():
                app.say(utt)

        app.add_poll_hook(poll)
        log.info("Dialogue vocal prêt (push-to-talk : %s)", cfg.voice.ptt_kind)

    app.on_ready(start)


def build_app(cfg: AppConfig, echo: Callable[[str], None] | None = None) -> SimEngineerApp:
    speaker = make_speaker(cfg, echo=echo, on_started=_log_latency)
    app = SimEngineerApp(cfg, make_source(cfg.source), speaker)
    attach_dialogue(app)
    return app
