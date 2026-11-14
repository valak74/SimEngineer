"""Service de dialogue : thread dédié, jamais dans la boucle temps réel.

Les questions (texte transcrit) entrent par `ask` ; les réponses sortent par `poll`
et sont dites en priorité P3 par l'arbitre.
"""

from __future__ import annotations

import queue
import threading

from simengineer.core.log import get_logger
from simengineer.llm.dialogue import DialogueEngine, DialogueResult
from simengineer.voice.arbiter import Priority, Utterance

log = get_logger("dialogue")


class DialogueService:
    def __init__(self, engine: DialogueEngine) -> None:
        self.engine = engine
        self._in: queue.Queue[str | None] = queue.Queue()
        self._out: queue.Queue[DialogueResult] = queue.Queue()
        self._n = 0
        self._thread = threading.Thread(target=self._run, name="dialogue", daemon=True)
        self._thread.start()

    def ask(self, question: str) -> None:
        self._in.put(question)

    def _run(self) -> None:
        while True:
            question = self._in.get()
            if question is None:
                return
            try:
                result = self.engine.answer(question)
            except Exception:
                log.exception("Erreur de dialogue")
                result = self.engine.local_answer(question, "erreur")
            log.info("Réponse (%s) : %s", result.source, result.text)
            self._out.put(result)

    def poll(self) -> list[Utterance]:
        out: list[Utterance] = []
        while True:
            try:
                res = self._out.get_nowait()
            except queue.Empty:
                return out
            self._n += 1
            out.append(Utterance(Priority.P3, f"dialogue:{self._n}", res.text, ttl_s=20.0))

    def close(self) -> None:
        self._in.put(None)
        self._thread.join(timeout=2.0)
