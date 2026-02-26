"""Configuration management: TOML defaults + user overrides + env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config.default.toml"
_USER_CONFIG = Path("~/.config/giva/config.toml").expanduser()
_SECRETS_FILE = Path("~/.config/giva/secrets.toml").expanduser()


@dataclass(frozen=True)
class MailConfig:
    mailboxes: list[str] = field(
        default_factory=lambda: ["INBOX", "Sent", "Drafts", "Archive"]
    )
    batch_size: int = 50
    sync_interval_minutes: int = 15
    initial_sync_months: int = 4        # months of history for bootstrap sync
    deep_sync_max_months: int = 24      # max lookback for incremental deepening
    writing_style_sample_size: int = 20  # sent emails to sample for style analysis


@dataclass(frozen=True)
class CalendarConfig:
    sync_window_past_days: int = 7
    sync_window_future_days: int = 30
    sync_interval_minutes: int = 15


@dataclass(frozen=True)
class LLMConfig:
    model: str = "mlx-community/Qwen3-30B-A3B-4bit"  # Assistant (large)
    filter_model: str = "mlx-community/Qwen3-8B-4bit"  # Filter (small, fast)
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    context_budget_tokens: int = 8000


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    tts_model: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
    tts_voice: str = "af_heart"
    stt_model: str = "distil-medium.en"
    sample_rate: int = 24000


@dataclass(frozen=True)
class GoalsConfig:
    strategy_interval_hours: int = 6
    daily_review_hour: int = 18
    max_strategies_per_run: int = 1
    plan_horizon_days: int = 7
    # Weekly reflection: day (0=Mon..6=Sun) and hour
    weekly_reflection_day: int = 6  # Sunday
    weekly_reflection_hour: int = 18


@dataclass(frozen=True)
class AgentsConfig:
    enabled: bool = True
    routing_enabled: bool = True  # False to disable LLM routing (manual-only)
    max_execution_seconds: int = 60
    # Orchestrator: max wall-clock seconds for full plan+execute+synthesize cycle
    orchestrator_timeout_seconds: int = 180
    # Orchestrator: max subtasks the planner is allowed to create
    orchestrator_max_subtasks: int = 6
    # Scheduler: enable periodic agent tasks (opt-in)
    scheduler_agent_enabled: bool = False
    # Scheduler: how often to check for automated agent work (minutes)
    scheduler_agent_interval_minutes: int = 60


@dataclass(frozen=True)
class PowerConfig:
    enabled: bool = True
    battery_pause_threshold: int = 20       # Below this %, skip ALL background work
    battery_defer_heavy_threshold: int = 50  # Below this %, skip heavy work
    thermal_pause_threshold: int = 3        # At this thermal state, skip ALL work
    thermal_defer_heavy_threshold: int = 2  # At this thermal state, skip heavy work
    model_idle_timeout_minutes: int = 20    # Unload models after N idle minutes


@dataclass(frozen=True)
class GivaConfig:
    data_dir: Path = field(default_factory=lambda: Path("~/.local/share/giva").expanduser())
    log_level: str = "INFO"
    mail: MailConfig = field(default_factory=MailConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    goals: GoalsConfig = field(default_factory=GoalsConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    power: PowerConfig = field(default_factory=PowerConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "giva.db"


def _to_bool(val) -> bool:
    """Convert a config value to bool (handles TOML bools + string env vars)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts."""
    merged = base.copy()
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _apply_env(raw: dict) -> dict:
    """Override config values from GIVA_ environment variables."""
    env_map = {
        "GIVA_DATA_DIR": ("giva", "data_dir"),
        "GIVA_LOG_LEVEL": ("giva", "log_level"),
        "GIVA_MAIL_BATCH_SIZE": ("mail", "batch_size"),
        "GIVA_MAIL_SYNC_INTERVAL": ("mail", "sync_interval_minutes"),
        "GIVA_LLM_MODEL": ("llm", "model"),
        "GIVA_LLM_FILTER_MODEL": ("llm", "filter_model"),
        "GIVA_LLM_MAX_TOKENS": ("llm", "max_tokens"),
        "GIVA_LLM_TEMPERATURE": ("llm", "temperature"),
        "GIVA_VOICE_ENABLED": ("voice", "enabled"),
        "GIVA_VOICE_TTS_MODEL": ("voice", "tts_model"),
        "GIVA_VOICE_STT_MODEL": ("voice", "stt_model"),
        "GIVA_GOALS_STRATEGY_INTERVAL": ("goals", "strategy_interval_hours"),
        "GIVA_GOALS_REVIEW_HOUR": ("goals", "daily_review_hour"),
        "GIVA_AGENTS_ENABLED": ("agents", "enabled"),
        "GIVA_AGENTS_ROUTING": ("agents", "routing_enabled"),
        "GIVA_POWER_ENABLED": ("power", "enabled"),
    }
    for env_key, path in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            section, key = path
            if section not in raw:
                raw[section] = {}
            raw[section][key] = val
    return raw


def save_config(updates: dict) -> None:
    """Persist arbitrary config section updates to the user config file.

    ``updates`` is a dict of ``{section: {key: value, ...}, ...}``.
    Creates or updates ~/.config/giva/config.toml, merging with existing content.
    Next load_config() call will pick up the changes.
    """
    _USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {}
    if _USER_CONFIG.exists():
        with open(_USER_CONFIG, "rb") as f:
            raw = tomllib.load(f)

    raw = _deep_merge(raw, updates)
    _write_toml(_USER_CONFIG, raw)


def save_llm_config(model: str, filter_model: str) -> None:
    """Persist LLM model choices to the user config file.

    Creates or updates ~/.config/giva/config.toml with the [llm] section.
    Next load_config() call will pick up the changes.
    """
    _USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config or start fresh
    raw: dict = {}
    if _USER_CONFIG.exists():
        with open(_USER_CONFIG, "rb") as f:
            raw = tomllib.load(f)

    # Update llm section
    if "llm" not in raw:
        raw["llm"] = {}
    raw["llm"]["model"] = model
    raw["llm"]["filter_model"] = filter_model

    # Write back as TOML
    _write_toml(_USER_CONFIG, raw)


def _write_toml(path: Path, data: dict) -> None:
    """Write a dict as TOML to a file (simple serializer for flat/nested dicts)."""
    lines = []
    # Write top-level non-dict keys first
    for key, val in data.items():
        if not isinstance(val, dict):
            lines.append(f"{key} = {_toml_value(val)}")
    if lines:
        lines.append("")

    # Write sections
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append(f"[{key}]")
            for k, v in val.items():
                lines.append(f"{k} = {_toml_value(v)}")
            lines.append("")

    path.write_text("\n".join(lines))


def _toml_value(val) -> str:
    """Format a Python value as a TOML value string."""
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        items = ", ".join(_toml_value(v) for v in val)
        return f"[{items}]"
    return f'"{val}"'


def _load_secrets() -> dict[str, str]:
    """Load secrets from ~/.config/giva/secrets.toml.

    Returns a flat dict of secret_name → value from the ``[secrets]`` section.
    Returns an empty dict if the file doesn't exist or has no ``[secrets]`` section.
    """
    if not _SECRETS_FILE.exists():
        return {}
    try:
        with open(_SECRETS_FILE, "rb") as f:
            raw = tomllib.load(f)
        secrets = raw.get("secrets", {})
        return {k: str(v) for k, v in secrets.items()}
    except Exception:
        return {}


def _resolve_secrets(raw: dict, secrets: dict[str, str]) -> dict:
    """Resolve ``$SECRET_NAME`` references in MCP server env values.

    Scans ``[mcp_servers.<name>].env`` dicts and replaces any string value
    that starts with ``$`` with the matching value from ``secrets``.  Missing
    secrets are logged and the entry is removed so the server can fail
    gracefully rather than passing the literal ``$TOKEN`` string.
    """
    import logging
    log = logging.getLogger(__name__)

    mcp = raw.get("mcp_servers")
    if not mcp or not isinstance(mcp, dict):
        return raw

    for server_name, server_cfg in mcp.items():
        if not isinstance(server_cfg, dict):
            continue
        env = server_cfg.get("env")
        if not isinstance(env, dict):
            continue

        resolved = {}
        for key, val in env.items():
            if isinstance(val, str) and val.startswith("$"):
                secret_key = val[1:]  # strip leading $
                if secret_key in secrets:
                    resolved[key] = secrets[secret_key]
                else:
                    log.warning(
                        "mcp_servers.%s.env.%s: secret '%s' not found in "
                        "~/.config/giva/secrets.toml — server may fail to start",
                        server_name, key, secret_key,
                    )
                    # Omit the key so the subprocess gets no value rather than "$NAME"
            else:
                resolved[key] = val
        server_cfg["env"] = resolved

    return raw


def load_raw_config() -> dict:
    """Load and merge raw TOML config (without parsing into GivaConfig).

    Applies: config.default.toml → user config.toml → secrets.toml → GIVA_* env vars.
    Useful when subsystems (e.g. MCP agents) need access to sections
    that are not represented in the typed :class:`GivaConfig`.
    """
    raw: dict = {}

    if _DEFAULT_CONFIG.exists():
        with open(_DEFAULT_CONFIG, "rb") as f:
            raw = tomllib.load(f)

    if _USER_CONFIG.exists():
        with open(_USER_CONFIG, "rb") as f:
            user = tomllib.load(f)
        raw = _deep_merge(raw, user)

    secrets = _load_secrets()
    if secrets:
        raw = _resolve_secrets(raw, secrets)

    return _apply_env(raw)


def load_config() -> GivaConfig:
    """Load configuration from defaults, user file, and environment."""
    raw = load_raw_config()

    giva_raw = raw.get("giva", {})
    data_dir = Path(giva_raw.get("data_dir", "~/.local/share/giva")).expanduser()

    return GivaConfig(
        data_dir=data_dir,
        log_level=giva_raw.get("log_level", "INFO"),
        mail=MailConfig(
            mailboxes=raw.get("mail", {}).get(
                "mailboxes", ["INBOX", "Sent", "Drafts", "Archive"]
            ),
            batch_size=int(raw.get("mail", {}).get("batch_size", 50)),
            sync_interval_minutes=int(raw.get("mail", {}).get("sync_interval_minutes", 15)),
            initial_sync_months=int(
                raw.get("mail", {}).get("initial_sync_months", 4)
            ),
            deep_sync_max_months=int(
                raw.get("mail", {}).get("deep_sync_max_months", 24)
            ),
            writing_style_sample_size=int(
                raw.get("mail", {}).get("writing_style_sample_size", 20)
            ),
        ),
        calendar=CalendarConfig(
            sync_window_past_days=int(raw.get("calendar", {}).get("sync_window_past_days", 7)),
            sync_window_future_days=int(raw.get("calendar", {}).get("sync_window_future_days", 30)),
            sync_interval_minutes=int(
                raw.get("calendar", {}).get("sync_interval_minutes", 15)
            ),
        ),
        llm=LLMConfig(
            model=raw.get("llm", {}).get("model", "mlx-community/Qwen3-30B-A3B-4bit"),
            filter_model=raw.get("llm", {}).get("filter_model", "mlx-community/Qwen3-8B-4bit"),
            max_tokens=int(raw.get("llm", {}).get("max_tokens", 2048)),
            temperature=float(raw.get("llm", {}).get("temperature", 0.7)),
            top_p=float(raw.get("llm", {}).get("top_p", 0.9)),
            context_budget_tokens=int(raw.get("llm", {}).get("context_budget_tokens", 8000)),
        ),
        voice=VoiceConfig(
            enabled=_to_bool(raw.get("voice", {}).get("enabled", False)),
            tts_model=raw.get("voice", {}).get(
                "tts_model", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
            ),
            tts_voice=raw.get("voice", {}).get("tts_voice", "af_heart"),
            stt_model=raw.get("voice", {}).get("stt_model", "distil-medium.en"),
            sample_rate=int(raw.get("voice", {}).get("sample_rate", 24000)),
        ),
        goals=GoalsConfig(
            strategy_interval_hours=int(
                raw.get("goals", {}).get("strategy_interval_hours", 6)
            ),
            daily_review_hour=int(raw.get("goals", {}).get("daily_review_hour", 18)),
            max_strategies_per_run=int(
                raw.get("goals", {}).get("max_strategies_per_run", 1)
            ),
            plan_horizon_days=int(raw.get("goals", {}).get("plan_horizon_days", 7)),
        ),
        agents=AgentsConfig(
            enabled=_to_bool(raw.get("agents", {}).get("enabled", True)),
            routing_enabled=_to_bool(raw.get("agents", {}).get("routing_enabled", True)),
            max_execution_seconds=int(
                raw.get("agents", {}).get("max_execution_seconds", 60)
            ),
            orchestrator_timeout_seconds=int(
                raw.get("agents", {}).get("orchestrator_timeout_seconds", 180)
            ),
            orchestrator_max_subtasks=int(
                raw.get("agents", {}).get("orchestrator_max_subtasks", 6)
            ),
            scheduler_agent_enabled=_to_bool(
                raw.get("agents", {}).get("scheduler_agent_enabled", False)
            ),
            scheduler_agent_interval_minutes=int(
                raw.get("agents", {}).get("scheduler_agent_interval_minutes", 60)
            ),
        ),
        power=PowerConfig(
            enabled=_to_bool(raw.get("power", {}).get("enabled", True)),
            battery_pause_threshold=int(
                raw.get("power", {}).get("battery_pause_threshold", 20)
            ),
            battery_defer_heavy_threshold=int(
                raw.get("power", {}).get("battery_defer_heavy_threshold", 50)
            ),
            thermal_pause_threshold=int(
                raw.get("power", {}).get("thermal_pause_threshold", 3)
            ),
            thermal_defer_heavy_threshold=int(
                raw.get("power", {}).get("thermal_defer_heavy_threshold", 2)
            ),
            model_idle_timeout_minutes=int(
                raw.get("power", {}).get("model_idle_timeout_minutes", 20)
            ),
        ),
    )
