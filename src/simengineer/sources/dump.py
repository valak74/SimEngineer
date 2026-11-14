"""`dump-vars` : documente les variables réellement exposées par une source."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from simengineer.core.vars import VARS, VarsReport, check_vars
from simengineer.sources.base import TelemetrySource


def write_vars_doc(source: TelemetrySource, out: Path) -> VarsReport:
    described = source.available_vars()
    names = {d.name for d in described}
    report = check_vars(names)
    used = {s.name: s for s in VARS.values()}
    lines = [
        "# Variables de télémétrie",
        "",
        f"Généré par `simengineer dump-vars` — source : `{source.name}` — "
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
        "",
        "> Seul un fichier généré avec la source `live` (ou un vrai `.ibt`) fait foi pour"
        " vérifier les hypothèses de `docs/ASSUMPTIONS.md`.",
        "",
        "## Validation de `VARS`",
        "",
        "| Clé interne | Nom iRacing supposé | Obligatoire | Présente |",
        "|---|---|---|---|",
    ]
    for spec in VARS.values():
        ok = "oui" if spec.name in names else "**NON**"
        lines.append(
            f"| `{spec.key}` | `{spec.name}` | {'oui' if spec.required else 'non'} | {ok} |"
        )
    lines += [
        "",
        f"## Toutes les variables exposées ({len(described)})",
        "",
        "| Nom | Type | Taille | Unité | Description | Utilisée |",
        "|---|---|---|---|---|---|",
    ]
    for d in described:
        lines.append(
            f"| `{d.name}` | {d.type} | {d.count} | {d.unit} | {d.desc} |"
            f" {'oui' if d.name in used else ''} |"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
