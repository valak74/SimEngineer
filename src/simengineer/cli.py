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


if __name__ == "__main__":
    app()
