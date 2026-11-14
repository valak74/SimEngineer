"""Interface en ligne de commande (typer)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from simengineer.core.config import AppConfig, load_config, load_dotenv
from simengineer.core.log import get_logger, setup_logging

if TYPE_CHECKING:
    from simengineer.ui.server import UIServer

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
    from simengineer.voice.templates import corner_word, render

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
            (Priority.P2, "adv", render("adv_brake_later", lang, c=corner_word(4, lang), d=12)),
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
    speed: Annotated[
        float | None, typer.Option(help="Rejeu temps réel accéléré (démo), ex. 4.")
    ] = None,
    ui: Annotated[
        bool | None, typer.Option("--ui/--no-ui", help="Interface dans le navigateur.")
    ] = None,
    training: Annotated[
        bool,
        typer.Option("--training", help="Mode entraînement : rappel avant les virages ciblés."),
    ] = False,
    keep_ui: Annotated[
        bool, typer.Option("--keep-ui", help="Garder l'interface ouverte après la session.")
    ] = False,
) -> None:
    """Lance l'ingénieur de course (spotter, carburant, coach, dialogue, interface)."""
    from simengineer.runtime import build_app

    cfg = _load(config, source, ibt)
    if scenario:
        cfg.source.scenario = scenario
    if realtime is not None:
        cfg.source.realtime = realtime
    if speed is not None:
        cfg.source.speed = speed
    if ui is not None:
        cfg.ui.enabled = ui
    if training:
        cfg.coach.training_mode = True
    if null_voice:
        cfg.voice.tts_backend = "null"
        cfg.voice.ptt_kind = "none"
    server = _start_ui(cfg) if cfg.ui.enabled else None
    # Source live : après chaque session on se remet en attente d'iRacing (démarrage auto).
    keep_waiting = cfg.source.kind == "live" and cfg.source.wait_for_sim and max_frames is None
    try:
        while True:
            application = build_app(cfg, echo=typer.echo)
            if server is not None:
                server.attach(application)
            try:
                session_id = application.run(max_frames)
            except KeyboardInterrupt:
                typer.echo("Arrêt demandé.")
                break
            stats = application.stats()
            typer.echo(
                f"Session {session_id} terminée : {stats['frames']} images, "
                f"{stats['mean_frame_ms']} ms/image en moyenne, max {stats['max_frame_ms']} ms."
            )
            if not keep_waiting:
                break
        if server is not None and keep_ui:
            _serve_forever(server)
    finally:
        if server is not None:
            server.stop()


def _start_ui(cfg: AppConfig, inline: bool = False) -> UIServer | None:
    from simengineer.ui.server import UIServer

    server = UIServer(cfg, inline_queries=inline)
    try:
        server.start()
    except OSError as exc:
        typer.echo(f"Interface indisponible (port {cfg.ui.port} occupé ?) : {exc}")
        return None
    typer.echo(f"Interface : {server.url}")
    if cfg.ui.open_browser:
        import webbrowser

        webbrowser.open(server.url)
    return server


def _serve_forever(server: UIServer) -> None:
    import time

    typer.echo("Interface ouverte. Ctrl+C pour quitter.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return


@app.command("ui")
def ui_command(
    config: ConfigOpt = None,
    port: Annotated[int | None, typer.Option(help="Port (défaut : ui.port).")] = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Ne pas ouvrir le navigateur.")
    ] = False,
) -> None:
    """Ouvre l'interface (historique : séances, pilote, setup, références) sans rouler."""
    cfg = _load(config)
    if port is not None:
        cfg.ui.port = port
    if no_browser:
        cfg.ui.open_browser = False
    server = _start_ui(cfg)
    if server is None:
        raise typer.Exit(1)
    try:
        _serve_forever(server)
    finally:
        server.stop()


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


@app.command("ask")
def ask(
    question: Annotated[str, typer.Argument(help="Question à l'ingénieur.")],
    config: ConfigOpt = None,
    scenario: Annotated[str, typer.Option(help="Scénario synthétique rejoué avant.")] = "race",
    local: Annotated[bool, typer.Option("--local", help="Sans LLM (gabarits).")] = False,
) -> None:
    """Pose une question à l'ingénieur après un rejeu synthétique (test du dialogue/LLM)."""
    from simengineer.app import SimEngineerApp
    from simengineer.runtime import make_dialogue_engine
    from simengineer.sources.scenarios import get_scenario
    from simengineer.sources.synthetic import SyntheticSource

    cfg = _load(config)
    cfg.voice.tts_backend = "null"
    cfg.source.realtime = False
    if local:
        cfg.llm.enabled = False
    application = SimEngineerApp(cfg, SyntheticSource(get_scenario(scenario)))
    application.run(max_frames=60 * 60 * 9)
    result = make_dialogue_engine(application).answer(question)
    typer.echo(f"{result.text}\n  [source : {result.source} ; outils : {result.tools_used}]")


ref_app = typer.Typer(help="Tours de référence (fichiers que tu possèdes).", no_args_is_help=True)
app.add_typer(ref_app, name="ref")


@ref_app.command("import")
def ref_import(
    file: Annotated[Path, typer.Argument(help="Fichier .ibt ou .csv.")],
    source: Annotated[str, typer.Option(help="Provenance (ex. « mon tour », « pack acheté »).")],
    rights: Annotated[str, typer.Option(help="Droit d'usage (ex. « personnel », « licence X »).")],
    label: Annotated[str, typer.Option(help="Nom affiché.")] = "",
    track: Annotated[str, typer.Option(help="Circuit (CSV uniquement).")] = "",
    car: Annotated[str, typer.Option(help="Voiture (CSV uniquement).")] = "",
    track_length: Annotated[float | None, typer.Option(help="Longueur en m (CSV en %).")] = None,
    lap: Annotated[int | None, typer.Option(help="Numéro de tour (.ibt).")] = None,
    config: ConfigOpt = None,
) -> None:
    """Importe un tour de référence avec ses métadonnées de provenance et de droits."""
    from simengineer.reference.importer import import_csv, import_ibt
    from simengineer.store.db import Store
    from simengineer.voice.templates import fmt_lap_time

    cfg = _load(config)
    store = Store(cfg.data_dir)
    try:
        if file.suffix.lower() == ".ibt":
            row = import_ibt(store, file, source, rights, label, lap)
        else:
            row = import_csv(store, file, track, car, source, rights, label, track_length)
    except (ValueError, OSError) as exc:
        typer.echo(f"Import refusé : {exc}")
        raise typer.Exit(1) from exc
    typer.echo(
        f"Référence {row.id} importée : {row.track} / {row.car} / "
        f"{fmt_lap_time(row.lap_time, cfg.language)} ({row.source} ; {row.rights})"
    )


@ref_app.command("list")
def ref_list(config: ConfigOpt = None) -> None:
    """Liste les références importées."""
    from simengineer.reference.store import list_references
    from simengineer.store.db import Store

    cfg = _load(config)
    rows = list_references(Store(cfg.data_dir))
    if not rows:
        typer.echo("Aucune référence importée.")
    for r in rows:
        typer.echo(
            f"{r.id:>3}  {r.lap_time:8.3f}s  {r.track} / {r.car}  [{r.label}]  "
            f"source: {r.source} ; droits: {r.rights}"
        )


@ref_app.command("delete")
def ref_delete(ref_id: int, config: ConfigOpt = None) -> None:
    """Supprime une référence importée."""
    from simengineer.reference.store import delete_reference
    from simengineer.store.db import Store

    cfg = _load(config)
    delete_reference(Store(cfg.data_dir), ref_id)
    typer.echo(f"Référence {ref_id} supprimée.")


@app.command("sessions")
def sessions(config: ConfigOpt = None) -> None:
    """Liste les sessions enregistrées (noms exacts de circuit et de voiture)."""
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    for s in store.sessions():
        n = len(store.laps(s.id))
        typer.echo(
            f"{s.id:>4}  {s.started_at[:16]}  {s.track} / {s.car}  {s.session_type}  {n} tours"
        )


def _track_car(store: object, track: str, car: str) -> tuple[str, str]:
    from simengineer.store.db import Store

    assert isinstance(store, Store)
    if track and car:
        return track, car
    sess = store.session(_session_or_last(store, None))
    return track or sess.track, car or sess.car


@app.command("profile")
def profile(
    config: ConfigOpt = None,
    track: Annotated[str, typer.Option(help="Circuit (défaut : dernière session).")] = "",
    car: Annotated[str, typer.Option(help="Voiture (défaut : dernière session).")] = "",
) -> None:
    """Profil pilote : tendances par virage sur toutes les séances de ce circuit/voiture."""
    from simengineer.coach.profile import build_profile, render_profile
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    track, car = _track_car(store, track, car)
    prof = build_profile(store, track, car)
    if prof is None:
        typer.echo(f"Pas encore de tours analysés pour {track} / {car}.")
        raise typer.Exit(1)
    typer.echo(render_profile(prof, cfg.language))


@app.command("training")
def training(
    config: ConfigOpt = None,
    track: Annotated[str, typer.Option(help="Circuit (défaut : dernière session).")] = "",
    car: Annotated[str, typer.Option(help="Voiture (défaut : dernière session).")] = "",
) -> None:
    """Programme d'entraînement : un thème pour la prochaine séance."""
    from simengineer.coach.profile import build_profile
    from simengineer.coach.training import build_plan
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    track, car = _track_car(store, track, car)
    prof = build_profile(store, track, car)
    if prof is None:
        typer.echo(f"Pas encore de tours analysés pour {track} / {car}.")
        raise typer.Exit(1)
    key = f"plan_last:{track}:{car}"
    plan = build_plan(prof, cfg.language, avoid=store.kv_get(key))
    store.kv_set(key, plan.theme)
    typer.echo(plan.render(cfg.language))


track_app = typer.Typer(
    help="Virages d'un circuit : noms et bornes éditables.", no_args_is_help=True
)
app.add_typer(track_app, name="track")


def _track_and_length(store: object, track: str) -> tuple[str, float]:
    from simengineer.store.db import Store

    assert isinstance(store, Store)
    sessions = store.sessions(track=track) if track else store.sessions()
    if not sessions:
        typer.echo("Aucune séance enregistrée pour ce circuit. Roule d'abord quelques tours.")
        raise typer.Exit(1)
    last = sessions[-1]
    return last.track, last.track_length_m


@track_app.command("show")
def track_show(
    config: ConfigOpt = None,
    track: Annotated[str, typer.Option(help="Circuit (défaut : dernière séance).")] = "",
) -> None:
    """Affiche les virages utilisés pour un circuit (fichier du pilote ou détection)."""
    from simengineer.coach.trackmap import load_corners, track_file
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    name, length = _track_and_length(store, track)
    corners = load_corners(store, name, length)
    path = track_file(cfg.data_dir, name)
    origin = f"fichier {path}" if path.is_file() else "détection automatique"
    typer.echo(f"{name} ({length:.0f} m) — {origin}")
    if not corners:
        typer.echo("Pas encore de virages : roule un tour propre.")
        return
    for c in corners:
        typer.echo(
            f"  {c.name}  {c.label or '':<24} début {c.start_m:6.0f} m  corde {c.apex_m:6.0f} m"
            f"  fin {c.end_m:6.0f} m"
        )


@track_app.command("export")
def track_export(
    config: ConfigOpt = None,
    track: Annotated[str, typer.Option(help="Circuit (défaut : dernière séance).")] = "",
    force: Annotated[bool, typer.Option("--force", help="Écraser le fichier existant.")] = False,
) -> None:
    """Crée le fichier éditable des virages (pour les nommer ou corriger leurs bornes)."""
    from simengineer.coach.trackmap import TrackFileError, export_track_file
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    name, length = _track_and_length(store, track)
    try:
        path = export_track_file(store, name, length, force)
    except (FileExistsError, TrackFileError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(
        f"Fichier créé : {path}\nRemplis les « label », puis relance : il sera utilisé partout."
    )


setup_app = typer.Typer(
    help="Conseiller de setup (tu appliques les réglages toi-même).", no_args_is_help=True
)
app.add_typer(setup_app, name="setup")


@setup_app.command("diagnose")
def setup_diagnose(
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
) -> None:
    """Diagnostic sous/survirage par phase et pneus, sur les tours propres de la session."""
    from simengineer.coach.corners import detect_corners
    from simengineer.coach.trackmap import load_corners
    from simengineer.setup.diagnosis import diagnose
    from simengineer.setup.guide import render_diagnosis
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    sid = _session_or_last(store, session)
    grids = [store.load_grid(r.npz_path) for r in store.laps(sid, valid_only=True) if r.npz_path]
    if not grids:
        typer.echo("Aucun tour propre dans cette session.")
        raise typer.Exit(1)
    sess = store.session(sid)
    corners = load_corners(store, sess.track, sess.track_length_m) or detect_corners(
        grids[0], cfg.coach
    )
    temps = store.kv_get(f"tyres:{sid}") or {}
    result = diagnose(grids, corners, cfg.setup, {k: (v[0], v[1], v[2]) for k, v in temps.items()})
    typer.echo(render_diagnosis(result, cfg.language))


@setup_app.command("mark")
def setup_mark(
    description: Annotated[str, typer.Argument(help="Réglage changé, ex. « ARB avant -1 cran ».")],
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
) -> None:
    """Note un changement de réglage (les tours suivants seront comparés aux précédents)."""
    from simengineer.setup.compare import add_mark
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    mark = add_mark(store, _session_or_last(store, session), description)
    typer.echo(f"Changement noté après le tour {mark.after_lap} : {mark.description}")


@setup_app.command("compare")
def setup_compare(
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
) -> None:
    """Avant/après du dernier changement noté, sur tours propres."""
    from simengineer.setup.compare import compare_mark, render_comparison
    from simengineer.store.db import Store

    cfg = _load(config)
    store = Store(cfg.data_dir)
    result = compare_mark(store, _session_or_last(store, session), cfg.setup, cfg.coach)
    if result is None:
        typer.echo('Aucun changement noté. Utilise d\'abord `simengineer setup mark "…"`.')
        raise typer.Exit(1)
    typer.echo(render_comparison(result, cfg.language))


@setup_app.command("guide")
def setup_guide(config: ConfigOpt = None) -> None:
    """Comment appliquer un réglage à la main dans le garage iRacing."""
    from simengineer.setup.guide import apply_guide

    typer.echo(apply_guide(_load(config).language))


@app.command("measure-position")
def measure_position(
    config: ConfigOpt = None,
    session: Annotated[int | None, typer.Option(help="Session (défaut : la dernière).")] = None,
) -> None:
    """Mesure la répétabilité de la position Lat/Lon sur les tours propres d'une session."""
    from simengineer.store.db import Store
    from simengineer.telemetry.position import measure_position_precision

    cfg = _load(config)
    store = Store(cfg.data_dir)
    sid = _session_or_last(store, session)
    grids = [store.load_grid(r.npz_path) for r in store.laps(sid, valid_only=True) if r.npz_path]
    res = measure_position_precision(grids)
    if not res.available:
        typer.echo(
            f"Mesure impossible : {res.laps} tour(s) propre(s) avec Lat/Lon (il en faut 2)."
            " Si Lat/Lon sont absentes en direct, voir docs/irsdk_vars.md."
        )
        raise typer.Exit(1)
    typer.echo(f"Tours propres comparés           : {res.laps}")
    typer.echo(f"Pas de position le plus fin      : {res.quantization_m:.3f} m")
    typer.echo(f"Dispersion tour à tour (médiane) : {res.median_spread_m:.2f} m")
    typer.echo(f"Dispersion tour à tour (95 %)    : {res.p95_spread_m:.2f} m")
    typer.echo(f"Dispersion maximale              : {res.max_spread_m:.2f} m")
    typer.echo("La dispersion inclut tes écarts de trajectoire : borne haute de l'erreur du SDK.")


@app.command("perf")
def perf(
    config: ConfigOpt = None,
    source: SourceOpt = None,
    ibt: Annotated[Path | None, typer.Option(help="Fichier .ibt (source ibt_replay).")] = None,
    seconds: Annotated[float, typer.Option(help="Durée de la mesure.")] = 30.0,
    check: Annotated[bool, typer.Option("--check", help="Code de sortie 1 hors budget.")] = False,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Écrit le résultat en JSON.")
    ] = None,
    with_ui: Annotated[
        bool, typer.Option("--with-ui", help="Compte aussi l'interface et un client connecté.")
    ] = False,
) -> None:
    """Mesure CPU/RAM du collecteur en temps réel (budget : < 3 % d'un cœur, < 150 Mo)."""
    import json

    from simengineer.perf import measure, render

    cfg = _load(config, source, ibt)
    if cfg.source.kind == "synthetic" and not source:
        cfg.source.scenario = "perf"
    result = measure(cfg, seconds, with_ui)
    typer.echo(render(result))
    if json_out:
        json_out.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    if check and not result.within_budget:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
