"""Modification ciblée de `simengineer.toml` en préservant les commentaires et l'ordre."""

from __future__ import annotations

import re
from pathlib import Path


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def set_config_value(path: Path, section: str, key: str, value: object) -> None:
    """Écrit `key = value` dans `[section]` (crée la section ou la clé si besoin)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    new_line = f"{key} = {toml_value(value)}"
    header = re.compile(r"^\s*\[\s*([^\]\[]+?)\s*\]\s*(#.*)?$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    current: str | None = None
    start: int | None = None
    end = len(lines)
    for i, line in enumerate(lines):
        m = header.match(line)
        if m and not line.lstrip().startswith("[["):
            if current == section:
                end = i
                break
            current = m.group(1)
            if current == section:
                start = i
        elif line.lstrip().startswith("[["):
            if current == section:
                end = i
                break
            current = None
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", new_line]
    else:
        for i in range(start + 1, end):
            if key_re.match(lines[i]):
                comment = ""
                if "#" in lines[i].split("=", 1)[1]:
                    comment = "  #" + lines[i].split("=", 1)[1].split("#", 1)[1]
                lines[i] = new_line + comment
                break
        else:
            insert = end
            while insert > start + 1 and not lines[insert - 1].strip():
                insert -= 1
            lines.insert(insert, new_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
