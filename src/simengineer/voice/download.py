"""Téléchargement des voix Piper (rhasspy/piper-voices sur Hugging Face)."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from pathlib import Path

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = {
    "fr": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
}


def voice_files(lang: str, models_dir: Path) -> list[tuple[str, Path]]:
    rel = VOICES[lang]
    name = rel.rsplit("/", 1)[1]
    return [(f"{BASE}/{rel}{ext}", models_dir / f"{name}{ext}") for ext in (".onnx", ".onnx.json")]


def download_voices(
    models_dir: Path,
    langs: tuple[str, ...] = ("fr", "en"),
    fetch: Callable[[str, Path], None] | None = None,
    echo: Callable[[str], None] = print,
) -> list[Path]:
    """Télécharge les voix manquantes ; renvoie les fichiers présents à la fin."""
    models_dir.mkdir(parents=True, exist_ok=True)
    get = fetch or _fetch
    present = []
    for lang in langs:
        for url, target in voice_files(lang, models_dir):
            if target.is_file() and target.stat().st_size > 0:
                present.append(target)
                continue
            echo(f"Téléchargement de {target.name}…")
            tmp = target.with_suffix(target.suffix + ".part")
            try:
                get(url, tmp)
                tmp.replace(target)
                present.append(target)
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                echo(f"  échec ({exc}) : {url}")
    return present


def _fetch(url: str, target: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "SimEngineer"})
    with urllib.request.urlopen(req, timeout=60) as resp, target.open("wb") as fh:
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
