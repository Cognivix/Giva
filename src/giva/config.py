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


@dataclass(frozen=True)
class MailConfig:
    mailboxes: list[str] = field(default_factory=lambda: ["INBOX", "Sent"])
    batch_size: int = 50
    sync_interval_minutes: int = 15


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
class GivaConfig:
    data_dir: Path = field(default_factory=lambda: Path("~/.local/share/giva").expanduser())
    log_level: str = "INFO"
    mail: MailConfig = field(default_factory=MailConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "giva.db"


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
    }
    for env_key, path in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            section, key = path
            if section not in raw:
                raw[section] = {}
            raw[section][key] = val
    return raw


def load_config() -> GivaConfig:
    """Load configuration from defaults, user file, and environment."""
    raw: dict = {}

    if _DEFAULT_CONFIG.exists():
        with open(_DEFAULT_CONFIG, "rb") as f:
            raw = tomllib.load(f)

    if _USER_CONFIG.exists():
        with open(_USER_CONFIG, "rb") as f:
            user = tomllib.load(f)
        raw = _deep_merge(raw, user)

    raw = _apply_env(raw)

    giva_raw = raw.get("giva", {})
    data_dir = Path(giva_raw.get("data_dir", "~/.local/share/giva")).expanduser()

    return GivaConfig(
        data_dir=data_dir,
        log_level=giva_raw.get("log_level", "INFO"),
        mail=MailConfig(
            mailboxes=raw.get("mail", {}).get("mailboxes", ["INBOX", "Sent"]),
            batch_size=int(raw.get("mail", {}).get("batch_size", 50)),
            sync_interval_minutes=int(raw.get("mail", {}).get("sync_interval_minutes", 15)),
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
    )
