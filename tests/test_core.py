from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from simengineer.cli import app
from simengineer.core.config import AppConfig, api_key_for, load_config, load_dotenv
from simengineer.core.vars import VARS, VarsValidationError, check_vars, validate_vars


def test_defaults_are_safe() -> None:
    cfg = AppConfig()
    assert cfg.engineer.pit_commands_enabled is False  # règle 1
    assert cfg.llm.timeout_s == 8.0
    assert [p.name for p in cfg.llm.providers] == ["qwen", "deepseek"]


def test_toml_and_env_override(tmp_path: Path) -> None:
    (tmp_path / "simengineer.toml").write_text(
        'language = "en"\n[engineer]\nfuel_margin_laps = 2.0\n', encoding="utf-8"
    )
    env = {"SIMENGINEER__ENGINEER__FUEL_MARGIN_LAPS": "1.5", "SIMENGINEER__SOURCE__KIND": "live"}
    cfg = load_config(env=env, search_dir=tmp_path)
    assert cfg.language == "en"
    assert cfg.engineer.fuel_margin_laps == 1.5
    assert cfg.source.kind == "live"


def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text("[engineer]\nnope = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p, env={})


def test_api_keys_only_from_env(tmp_path: Path) -> None:
    cfg = AppConfig()
    assert api_key_for(cfg.llm.providers[0], env={}) is None
    dotenv = tmp_path / ".env"
    dotenv.write_text("# commentaire\nDASHSCOPE_API_KEY='abc'\n", encoding="utf-8")
    env: dict[str, str] = {}
    load_dotenv(dotenv, env)
    assert api_key_for(cfg.llm.providers[0], env=env) == "abc"
    assert "api_key" not in cfg.model_dump_json().replace("api_key_env", "")


def test_vars_validation() -> None:
    names = [s.name for s in VARS.values()]
    assert validate_vars(names).ok
    missing = [n for n in names if n != VARS["speed"].name]
    assert check_vars(missing).missing_required == (VARS["speed"].name,)
    with pytest.raises(VarsValidationError):
        validate_vars(missing)
    optional = [n for n in names if n != VARS["lat"].name]
    assert validate_vars(optional).missing_optional == (VARS["lat"].name,)


def test_no_hardcoded_irsdk_names_outside_vars() -> None:
    """Règle 7 : les noms de variables iRacing ne vivent que dans core/vars.py."""
    root = Path(__file__).parent.parent / "src" / "simengineer"
    names = {s.name for s in VARS.values()} - {"Lap", "Speed", "Brake", "Throttle", "Gear", "Lat"}
    offenders = []
    for file in root.rglob("*.py"):
        if file.name in ("vars.py", "session_yaml.py"):  # YAML : constantes Y_*
            continue
        text = file.read_text(encoding="utf-8")
        offenders += [(file.name, n) for n in names if f'"{n}"' in text or f"'{n}'" in text]
    assert offenders == []


def test_cli_dump_vars_synthetic(tmp_path: Path) -> None:
    out = tmp_path / "vars.md"
    res = CliRunner().invoke(app, ["dump-vars", "--source", "synthetic", "--out", str(out)])
    assert res.exit_code == 0, res.output
    text = out.read_text(encoding="utf-8")
    assert "`CarLeftRight`" in text and "synthetic" in text
