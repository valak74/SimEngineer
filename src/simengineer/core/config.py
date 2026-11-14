"""Configuration : fichier TOML + surcharges par variables d'environnement.

Ordre de priorité (du plus faible au plus fort) :
valeurs par défaut < simengineer.toml < simengineer.local.toml < SIMENGINEER__SECTION__CLE.

Les clés d'API ne sont jamais stockées dans la configuration : chaque fournisseur
déclare seulement le NOM de la variable d'environnement qui contient sa clé.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ENV_PREFIX = "SIMENGINEER__"
DEFAULT_CONFIG_FILES = ("simengineer.toml", "simengineer.local.toml")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Section):
    kind: Literal["live", "ibt_replay", "synthetic"] = "synthetic"
    ibt_path: str = ""
    realtime: bool = True
    scenario: str = "race"
    wait_for_sim: bool = True


class VoiceConfig(_Section):
    tts_backend: Literal["piper", "null"] = "piper"
    piper_voice_fr: str = "models/fr_FR-siwis-medium.onnx"
    piper_voice_en: str = "models/en_US-lessac-medium.onnx"
    output_device: str = ""
    input_device: str = ""
    stt_backend: Literal["faster_whisper", "none"] = "faster_whisper"
    stt_model: str = "small"
    stt_compute_type: str = "int8"
    ptt_kind: Literal["keyboard", "joystick", "none"] = "keyboard"
    ptt_key: str = "f13"
    ptt_joystick_index: int = 0
    ptt_button: int = 0
    max_record_s: float = 12.0


class SpotterConfig(_Section):
    enabled: bool = True
    # Voiture arrêtée devant
    stopped_alert_distance_m: float = 400.0
    stopped_speed_ms: float = 8.0
    stopped_min_player_speed_ms: float = 20.0
    stopped_confirm_s: float = 0.25
    stopped_repeat_distance_m: float = 150.0
    stopped_cooldown_s: float = 6.0
    # Voitures adjacentes
    side_confirm_frames: int = 3
    clear_confirm_s: float = 0.3
    side_repeat_s: float = 4.0
    # Accident : nombre de voitures lentes devant dans la zone
    accident_min_cars: int = 2
    accident_distance_m: float = 600.0
    accident_cooldown_s: float = 15.0
    offtrack_cooldown_s: float = 10.0
    alerts_log: str = "alerts.jsonl"


class EngineerConfig(_Section):
    fuel_window_laps: int = 5
    fuel_margin_laps: float = 1.0
    fuel_reserve_l: float = 0.3
    low_fuel_laps_warning: float = 3.0
    tyre_wear_warning_pct: float = 35.0
    track_temp_delta_c: float = 3.0
    pit_commands_enabled: bool = False
    long_yellow_min_s: float = 20.0


class CoachConfig(_Section):
    enabled: bool = True
    max_advice: int = 2
    min_gain_s: float = 0.03
    # Seuils de base (calibrés ensuite par le profil pilote)
    brake_point_m: float = 6.0
    brake_peak: float = 0.08
    brake_rise_s: float = 0.08
    trail_m: float = 8.0
    min_speed_kmh: float = 2.5
    throttle_pickup_m: float = 6.0
    exit_speed_kmh: float = 3.0
    steering_corrections: int = 2
    # Détection de virages
    corner_lat_accel_ms2: float = 4.0
    corner_min_length_m: float = 25.0
    corner_merge_gap_m: float = 40.0


class SetupConfig(_Section):
    steering_ratio: float = 12.0
    wheelbase_m: float = 2.46
    understeer_index: float = 1.6
    oversteer_events_per_lap: float = 3.0
    camber_spread_max_c: float = 14.0
    camber_spread_min_c: float = 3.0
    pressure_middle_delta_c: float = 4.0


class LLMProviderConfig(_Section):
    name: str
    base_url: str
    model: str
    report_model: str = ""
    api_key_env: str
    # USD par million de jetons (à vérifier, voir docs/ASSUMPTIONS.md)
    price_in_per_mtok: float = 0.10
    price_out_per_mtok: float = 0.40


def _default_providers() -> list[LLMProviderConfig]:
    return [
        LLMProviderConfig(
            name="qwen",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            model="qwen-flash",
            report_model="qwen-plus",
            api_key_env="DASHSCOPE_API_KEY",
            price_in_per_mtok=0.05,
            price_out_per_mtok=0.40,
        ),
        LLMProviderConfig(
            name="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            report_model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            price_in_per_mtok=0.28,
            price_out_per_mtok=0.42,
        ),
    ]


class LLMConfig(_Section):
    enabled: bool = True
    timeout_s: float = 8.0
    max_retries: int = 1
    max_tool_rounds: int = 3
    max_tokens: int = 200
    report_max_tokens: int = 900
    daily_budget_usd: float = 0.50
    providers: list[LLMProviderConfig] = Field(default_factory=_default_providers)


class LoggingConfig(_Section):
    level: str = "INFO"
    file: str = ""


class AppConfig(_Section):
    language: Literal["fr", "en"] = "fr"
    data_dir: Path = Path("data")
    source: SourceConfig = Field(default_factory=SourceConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    spotter: SpotterConfig = Field(default_factory=SpotterConfig)
    engineer: EngineerConfig = Field(default_factory=EngineerConfig)
    coach: CoachConfig = Field(default_factory=CoachConfig)
    setup: SetupConfig = Field(default_factory=SetupConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_env_value(raw: str) -> Any:
    """Interprète une valeur d'environnement : JSON si possible, sinon chaîne."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, raw in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        path = [p.lower() for p in name[len(ENV_PREFIX) :].split("__") if p]
        if not path:
            continue
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _parse_env_value(raw)
    return out


def load_dotenv(path: Path = Path(".env"), env: dict[str, str] | None = None) -> None:
    """Charge un fichier .env local (ignoré par git) sans écraser l'environnement."""
    target = os.environ if env is None else env
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in target:
            target[key] = value


def load_config(
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
    search_dir: Path | None = None,
) -> AppConfig:
    """Charge la configuration. `path` explicite, sinon fichiers par défaut du dossier courant."""
    environ = os.environ if env is None else env
    data: dict[str, Any] = {}
    files: list[Path]
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")
        files = [path]
    else:
        base = search_dir or Path.cwd()
        files = [base / name for name in DEFAULT_CONFIG_FILES if (base / name).is_file()]
    for file in files:
        with file.open("rb") as fh:
            data = _deep_merge(data, tomllib.load(fh))
    data = _deep_merge(data, _env_overrides(environ))
    return AppConfig.model_validate(data)


def api_key_for(provider: LLMProviderConfig, env: Mapping[str, str] | None = None) -> str | None:
    """Clé d'API lue uniquement dans l'environnement (jamais dans le code ni la config)."""
    environ = os.environ if env is None else env
    value = environ.get(provider.api_key_env, "").strip()
    return value or None
