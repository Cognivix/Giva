# Giva - Generative Intelligent Virtual Assistant

A macOS-native personal assistant that syncs emails and calendar from Apple Mail/Calendar,
runs local LLM inference via MLX, and provides a CLI + REST API + SwiftUI menu bar app.

## Quick Reference

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run CLI
giva

# Run API server (port 7483)
giva-server

# Run tests
pytest

# Lint
ruff check src/ tests/
```

## Architecture

```
src/giva/
├── cli.py              # Interactive REPL (prompt-toolkit + rich)
├── server.py           # FastAPI REST + SSE API on 127.0.0.1:7483
├── config.py           # TOML config: config.default.toml → ~/.config/giva/config.toml → GIVA_* env
├── db/
│   ├── models.py       # Dataclasses: Email, Event, Task, UserProfile
│   ├── store.py        # SQLite + FTS5 data layer (WAL mode)
│   └── migrations.py   # Schema version check
├── sync/
│   ├── mail.py         # Apple Mail sync via JXA, chunked headers + LLM filter
│   ├── calendar.py     # EventKit (fast, needs TCC grant) or AppleScript fallback
│   └── scheduler.py    # Background sync via threading.Timer
├── llm/
│   ├── engine.py       # MLX ModelManager: dual-model (assistant 30B + filter 8B)
│   ├── prompts.py      # All prompt templates
│   └── structured.py   # Pydantic models for structured LLM output
├── intelligence/
│   ├── queries.py      # NL query → FTS5 context retrieval → streamed LLM response
│   ├── tasks.py        # Task extraction from emails/events via LLM
│   ├── profile.py      # SQL-first user profile analytics + LLM topic extraction
│   └── proactive.py    # Priority suggestion engine
└── utils/
    ├── applescript.py   # osascript/JXA runner helpers
    └── email_parser.py  # MIME parsing utilities

GivaApp/                    # SwiftUI macOS menu bar app (Xcode project)
├── Services/
│   ├── BootstrapManager.swift  # First-run: venv creation, pip install, launchd daemon
│   ├── ServerManager.swift     # Connects to launchd-managed daemon (health polling)
│   └── APIService.swift        # URLSession wrapper + SSE streaming
├── Views/
│   ├── BootstrapView.swift     # Cooking spinner shown during first-run setup
│   ├── MainPanelView.swift     # Header + tabs + content + quick actions
│   ├── ChatView.swift          # Chat messages + input
│   ├── TaskListView.swift      # Task list with priority indicators
│   └── QuickActionsView.swift  # Bottom action bar
├── ViewModels/
│   └── GivaViewModel.swift     # Central state management
├── Models/
│   └── APIModels.swift         # Codable structs for API responses
└── GivaApp.swift               # App entry point (bootstrap → main UI)

scripts/                # One-shot utility scripts
tests/                  # pytest test suite mirroring src/ structure
```

## Key Design Decisions

- **Local-only**: all data stays on device in SQLite at `~/.local/share/giva/giva.db`
- **Dual LLM**: assistant model (Qwen3-30B-A3B-4bit) for queries; filter model (Qwen3-8B-4bit) for email classification during sync. Both via `mlx-lm`.
- **Lazy email bodies**: sync fetches headers only; bodies are fetched on-demand from Apple Mail when the LLM needs them
- **Fail-safe LLM parsing**: JSON extraction from LLM output uses regex + multi-level fallback (always defaults to "keep" on error)
- **Calendar dual backend**: EventKit (fast, native) if TCC access granted; AppleScript fallback (no dialog, background-safe)
- **SSE streaming**: server bridges sync generators to async SSE via `asyncio.Queue` + thread pool
- **Thread safety**: single `_llm_lock` serializes all LLM calls (MLX ModelManager is not thread-safe)
- **First-run bootstrap**: SwiftUI app auto-creates venv at `~/.local/share/giva/.venv`, pip-installs the project, and registers a `com.giva.server` launchd user agent for the API daemon
- **Daemon lifecycle**: server runs as a launchd user agent (auto-restart on crash, start at login). App connects via health polling, never spawns its own server process

## Configuration

Layers: `config.default.toml` → `~/.config/giva/config.toml` → `GIVA_*` env vars.

Key env overrides: `GIVA_LLM_MODEL`, `GIVA_LLM_FILTER_MODEL`, `GIVA_DATA_DIR`, `GIVA_LOG_LEVEL`.

## Testing

```bash
pytest                       # all tests
pytest tests/test_db/        # DB layer only
pytest -x -q                 # stop on first failure, quiet
```

Tests use `tmp_path` fixtures for isolated SQLite DBs. No real LLM or Apple Mail calls in tests.

## Conventions

- Python 3.11+, ruff line-length 100
- `src/` layout with `setuptools`
- Frozen dataclasses for config, mutable for DB models
- All LLM prompt templates live in `src/giva/llm/prompts.py`
- Structured LLM output uses Pydantic models in `src/giva/llm/structured.py`
- DB schema is in `Store.SCHEMA_SQL` constant (not separate SQL files)
