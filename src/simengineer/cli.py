"""Interface en ligne de commande (typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from simengineer.core.config import AppConfig, load_config, load_dotenv
from simengineer.core.log import get_logger, setup_logging

app = typer.Typer(
    help="SimEngineer : ingénieur de course IA local pour iRacing.",
    no_args_is_help=True,
    add_completion=False,
)
log = get_logger("cli")

ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Fichier TOML.")]
SourceOpt = Annotated[
    str | None, typer.Option("--source", "-s", help="live | ibt_replay | synthetic")
]


def _load(config: Path | None, source: str | None = None, ibt: Path | None = None) -> AppConfig:
    load_dotenv()
    cfg = load_config(config)
    if source:
        cfg.source.kind = source  # type: ignore[assignment]
        cfg = AppConfig.model_validate(cfg.model_dump())
    if ibt:
        cfg.source.ibt_path = str(ibt)
    setup_logging(cfg.logging.level, cfg.logging.file)
    return cfg


@app.callback()
def _main() -> None:
    """SimEngineer : ingénieur de course IA local pour iRacing."""


@app.command("dump-vars")
def dump_vars(
    config: ConfigOpt = None,
    source: SourceOpt = None,
    ibt: Annotated[Path | None, typer.Option(help="Fichier .ibt (source ibt_replay).")] = None,
    out: Annotated[Path, typer.Option(help="Fichier Markdown de sortie.")] = Path(
        "docs/irsdk_vars.md"
    ),
) -> None:
    """Écrit la liste réelle des variables de télémétrie et valide VARS (règle 7)."""
    from simengineer.sources.dump import write_vars_doc
    from simengineer.sources.factory import make_source

    cfg = _load(config, source, ibt)
    src = make_source(cfg.source)
    src.open()
    try:
        report = write_vars_doc(src, out)
    finally:
        src.close()
    typer.echo(f"{out} écrit ({src.name}).")
    if report.missing_required:
        typer.echo("OBLIGATOIRES ABSENTES : " + ", ".join(report.missing_required))
        raise typer.Exit(1)
    if report.missing_optional:
        typer.echo("Facultatives absentes : " + ", ".join(report.missing_optional))
    else:
        typer.echo("Toutes les variables de VARS sont présentes.")


@app.command("devices")
def devices() -> None:
    """Liste les périphériques audio (pour voice.output_device / voice.input_device)."""
    from simengineer.voice.audio import AudioUnavailableError, list_devices

    try:
        for d in list_devices():
            typer.echo(f"{d['index']:>3}  in={d['inputs']} out={d['outputs']}  {d['name']}")
    except AudioUnavailableError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@app.command("say-test")
def say_test(
    config: ConfigOpt = None,
    text: Annotated[str | None, typer.Option(help="Phrase libre à prononcer.")] = None,
    null: Annotated[bool, typer.Option("--null", help="Sans audio : affiche le texte.")] = False,
) -> None:
    """Teste la voix : une alerte P0, une info P1, un conseil P2 (et mesure la latence)."""
    import time

    from simengineer.voice.arbiter import Priority, SpeechArbiter, Utterance
    from simengineer.voice.factory import make_speaker
    from simengineer.voice.templates import render

    cfg = _load(config)
    if null:
        cfg.voice.tts_backend = "null"

    def started(utt: Utterance, ms: float) -> None:
        typer.echo(f"  latence donnée -> son : {ms:.0f} ms ({utt.key})")

    speaker = make_speaker(cfg, echo=typer.echo, on_started=started)
    arbiter = SpeechArbiter(speaker)
    lang = cfg.language
    lines = (
        [(Priority.P3, "custom", text)]
        if text
        else [
            (Priority.P0, "car_left", render("car_left", lang)),
            (Priority.P0, "stopped", render("stopped_ahead", lang, dist=300)),
            (Priority.P1, "fuel", render("fuel_laps", lang, laps=12)),
            (Priority.P2, "adv", render("adv_brake_later", lang, n=4, d=12)),
        ]
    )
    for prio, key, phrase in lines:
        arbiter.submit(Utterance(prio, key, phrase, ttl_s=60.0, data_time=time.perf_counter()))
    deadline = time.monotonic() + 60.0
    while (arbiter.pending() or speaker.busy()) and time.monotonic() < deadline:
        arbiter.tick()
        time.sleep(0.02)
    typer.echo("Test de voix terminé.")


@app.command("run")
def run(
    config: ConfigOpt = None,
    source: SourceOpt = None,
    ibt: Annotated[Path | None, typer.Option(help="Fichier .ibt (source ibt_replay).")] = None,
    scenario: Annotated[str | None, typer.Option(help="Scénario synthétique.")] = None,
    realtime: Annotated[
        bool | None, typer.Option("--realtime/--no-realtime", help="Rejeu en temps réel.")
    ] = None,
    null_voice: Annotated[
        bool, typer.Option("--null-voice", help="Sans audio : affiche ce qui serait dit.")
    ] = False,
    max_frames: Annotated[int | None, typer.Option(help="Arrêt après N images.")] = None,
) -> None:
    """Lance l'ingénieur de course (spotter, carburant, coach, dialogue)."""
    from simengineer.runtime import build_app

    cfg = _load(config, source, ibt)
    if scenario:
        cfg.source.scenario = scenario
    if realtime is not None:
        cfg.source.realtime = realtime
    if null_voice:
        cfg.voice.tts_backend = "null"
        cfg.voice.ptt_kind = "none"
    application = build_app(cfg, echo=typer.echo)
    try:
        session_id = application.run(max_frames)
    except KeyboardInterrupt:
        session_id = application.session_id
        typer.echo("Arrêt demandé.")
    stats = application.stats()
    typer.echo(
        f"Session {session_id} terminée : {stats['frames']} images, "
        f"{stats['mean_frame_ms']} ms/image en moyenne, max {stats['max_frame_ms']} ms."
    )


def _session_or_last(store: object, session: int | None) -> int:
    from simengineer.store.db import Store

    assert isinstance(store, Store)
    sid = session if session is not None else store.last_session_id()
    if sid is None:
        typer.echo("Aucune session enregistrée. Lance d'abord `simengineer run`.")
        raise typer.Exit(1)
    return sid


@app.command("analyze")
def analyze(
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
    ibt: Annotated[Path | None, typer.Option(help="Analyser d'abord ce fichier .ibt.")] = None,
    top: Annotated[int, typer.Option(help="Nombre de conseils par tour.")] = 3,
) -> None:
    """Analyse par virage d'une session : conseils chiffrés classés par temps gagné."""
    from simengineer.coach.pipeline import reanalyze_session
    from simengineer.runtime import build_app
    from simengineer.store.db import Store
    from simengineer.voice.templates import fmt_delta, fmt_lap_time

    cfg = _load(config, "ibt_replay" if ibt else None, ibt)
    if ibt:
        cfg.source.realtime = False
        cfg.voice.tts_backend = "null"
        cfg.voice.ptt_kind = "none"
        session = build_app(cfg).run()
    store = Store(cfg.data_dir)
    sid = _session_or_last(store, session)
    label, analyses = reanalyze_session(store, sid, cfg.coach, cfg.language)
    sess = store.session(sid)
    typer.echo(f"Session {sid} — {sess.track} — {sess.car} — référence : {label or 'aucune'}")
    if not analyses:
        typer.echo("Pas assez de tours valides pour comparer (il en faut au moins deux).")
        return
    for a in analyses:
        lap_time = fmt_lap_time(a.lap_time, cfg.language)
        typer.echo(f"\nTour {a.lap_number} : {lap_time} ({fmt_delta(a.delta_s, cfg.language)} s)")
        if not a.advice:
            typer.echo("  rien à signaler")
        for adv in a.top(top):
            typer.echo(f"  - {adv.long}")


@app.command("report")
def report(
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
    out: Annotated[Path | None, typer.Option(help="Fichier Markdown de sortie.")] = None,
    llm: Annotated[
        bool, typer.Option("--llm/--no-llm", help="Ajouter un commentaire rédigé par le LLM.")
    ] = False,
) -> None:
    """Rapport de session en Markdown (gabarit ; commentaire LLM optionnel)."""
    from simengineer.coach.report import build_session_summary, render_report
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    sid = _session_or_last(store, session)
    summary = build_session_summary(store, sid)
    narrative = ""
    if llm and cfg.llm.enabled:
        from simengineer.llm.report import llm_narrative

        narrative = llm_narrative(cfg, summary)
    text = render_report(summary, cfg.language, narrative)
    target = out or cfg.data_dir / "reports" / f"session_{sid:05d}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    typer.echo(text)
    typer.echo(f"Rapport écrit : {target}")


if __name__ == "__main__":
    app()
