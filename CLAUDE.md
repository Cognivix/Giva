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
│   ├── models.py       # Dataclasses: Email, Event, Task, UserProfile, Goal, GoalStrategy
│   ├── store.py        # SQLite + FTS5 data layer (WAL mode, schema v4)
│   └── migrations.py   # Schema versioning + ALTER migrations
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
│   ├── proactive.py    # Priority suggestion engine
│   ├── onboarding.py   # Multi-step conversational onboarding with profile extraction
│   ├── goals.py        # Goal CRUD, strategy generation, progress tracking
│   ├── agents.py       # Post-chat agent pipeline: intent detection + action routing
│   ├── context.py      # Budget-aware context assembly + conversation memory
│   └── daily_review.py # Daily goal review + reflection
└── utils/
    ├── applescript.py   # osascript/JXA runner helpers
    └── email_parser.py  # MIME parsing utilities

GivaApp/                    # SwiftUI macOS menu bar app (Xcode project)
├── Services/
│   ├── BootstrapManager.swift  # First-run: venv creation, pip install, launchd daemon
│   ├── ServerManager.swift     # Connects to launchd-managed daemon (health polling)
│   ├── APIService.swift        # URLSession wrapper + SSE streaming
│   └── FileLogger.swift        # Dual-destination logger: os.Logger + file (~/.local/share/giva/logs/)
├── Views/
│   ├── BootstrapView.swift     # Cooking spinner shown during first-run setup
│   ├── MainPanelView.swift     # Header + tabs + content + quick actions
│   ├── ChatView.swift          # Chat messages + input + MarkdownText renderer
│   ├── TaskListView.swift      # Task list with priority indicators
│   ├── GoalsWindowView.swift   # Goals detail window (strategy, objectives, goal chat)
│   └── QuickActionsView.swift  # Bottom action bar
├── ViewModels/
│   ├── GivaViewModel.swift     # Central state management
│   └── GoalsViewModel.swift    # Goals window state (CRUD, chat, strategies)
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
- **One state machine, server-side**: The server's `bootstrap.checkpoint` is the single authoritative state (unknown → downloading_default_model → awaiting_model_selection → downloading_user_models → validating → ready → syncing → onboarding → operational). The SwiftUI app is a thin observer — it mirrors the phase, never drives transitions. ViewModel has no shadow booleans; all UI derives from `serverPhase`. Client-side flags (`isResetting`, `isUpgrading`) are transient overlays, not part of the state machine. On reset/upgrade, the client disconnects, kicks the daemon, and hands control back to bootstrap observation — no client-side reconnect logic.
- **SSE byte-level parsing**: Swift's `AsyncLineSequence` (`bytes.lines`) silently drops empty lines, which are SSE event delimiters. The SSE parser reads raw bytes and splits on `\n` manually to preserve empty lines. Never use `bytes.lines` for SSE.
- **Daemon restart port-polling**: `launchctl bootout` is async w.r.t. process termination. `BootstrapManager.bootoutIfLoaded()` polls port 7483 availability (via `socket()`+`bind()` probe) every 0.25s up to 15s before starting the new process. The launchd plist sets `ExitTimeOut: 5` to SIGKILL after 5s if SIGTERM doesn't work. `restartDaemon()` is async to keep the UI responsive.
- **Goal-scoped conversations**: The `conversations` table has a nullable `goal_id` column. Global chat uses `WHERE goal_id IS NULL`; goal chat uses `WHERE goal_id = ?`. The `handle_query()` function separates the original query (saved to DB) from the enriched context prefix (sent to LLM only). Conversation compression only touches global messages.
- **Post-chat agents in goal chat**: The `/api/goals/{goal_id}/chat` endpoint runs the same post-chat agent pipeline as regular chat. The agent prompt includes the goal context and supports `create_objective` intents (auto-creating child goals with tier inferred from parent). Agent actions are broadcast via `agent_actions` SSE events.

## Agent Architecture

> Full design: [`docs/agent-architecture.md`](docs/agent-architecture.md)

### Core Principles

1. **Lean main agent, rich secondary agents** — The chat LLM answers the user's question and moves on. Heavy lifting (task extraction, progress detection, fact learning, conversation compression) is delegated to secondary agents running the filter model post-response. The chat LLM is told about background agents so it doesn't try to do everything itself.

2. **Model assignment rule** — **Filter model** (≤8B): classification, extraction, structured JSON. **Assistant model** (30B+): judgment, synthesis, creativity, multi-step reasoning. Filter handles high-frequency per-turn work; assistant handles low-frequency high-value work.

3. **Context is a budget, not a dump** — Every token costs inference time and attention. Context is budgeted with fixed slot allocation (system 5%, query 5%, conversation 25%, retrieved 55%, headroom 10%), not dumped wholesale. The DB is extended memory — pull detail on demand via FTS.

4. **Adaptive to model size** — Context budget scales with model params: ≤1B→2000 tok, ≤8B→4000, ≤32B→8000, >32B→12000. If `context_budget_tokens` is explicitly set in config, that overrides auto-detection.

### Conversation Memory — Three Tiers

- **Tier 1 — Active Window**: last N turns, raw text. 70% of conversation budget. Oldest turns evict to Tier 2.
- **Tier 2 — Session Summary**: running summary of today's session, compressed by filter model. 30% of conversation budget. Resets at daily review.
- **Tier 3 — Learned Facts**: permanent user preferences extracted from session summaries during daily review. Always in system prompt as part of profile.

### Post-Chat Agent Pipeline

After every chat response — both global and goal chat — (filter model, single call, ~0.5s):
- **Intent Detector** — detects `create_task`, `create_objective`, `complete_task`, `progress`, `preference` intents
- **Conversation Tagger** — classifies the topic for session tracking
- **Progress Detector** — logs goal progress from chat content

In goal chat, the agent receives the goal context and auto-links created tasks/objectives to the current goal. `create_objective` auto-infers the child tier from the parent (long_term→mid_term, mid_term→short_term).

Actions are routed automatically. The chat LLM never emits structured action tags — the Intent Detector parses meaning from natural language.

### Knowledge Flow

- **Upward**: completed tasks → objective progress → goal milestones → weekly reflection
- **Downward**: long-term goals → strategy → objectives → tasks
- **Auto-linking**: filter model matches new tasks to goals on creation (link only if high confidence)

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
- **Prompt design**: chat prompts enforce brevity and reference background agents. Onboarding prompts require visible text BEFORE any `<profile_update>` tag block.
- **Agent design**: new agents use the filter model unless they need reasoning/synthesis. Post-chat agents are batched into a single LLM call to minimize lock contention. See `docs/agent-architecture.md` for routing tables.
- **SwiftUI UI**: Apple HIG — progressive disclosure (system actions in gear menu, daily actions in bottom bar), content-first layout, `serverPhase` as single source of truth. Full guidelines in `docs/agent-architecture.md` § 7.
- **No system dialogs in menu bar apps**: `.confirmationDialog`, `.alert`, and `.sheet` do not work reliably inside `MenuBarExtra(.window)` popovers — they appear behind the popover, fail to dismiss, or never show at all. **Always use inline confirmation banners** embedded in the view hierarchy instead. See `MainPanelView.confirmationBanner(for:)` for the pattern.
- **Xcode project file**: When adding or removing `.swift` files outside of Xcode, you **must** manually update `GivaApp.xcodeproj/project.pbxproj`. Each new file requires entries in four places: (1) `PBXBuildFile` — a build reference pointing to the file reference, (2) `PBXFileReference` — the file's identity and type, (3) `PBXGroup` — add the file reference to its parent group's `children` list, (4) `PBXSourcesBuildPhase` — add the build reference to the `files` list. Follow the ID convention of existing entries (e.g., `A1xxxxxx` for build files, `A2xxxxxx` for file references). Forgetting any of these causes "Cannot find X in scope" build errors.

## Logging

All logs live in `~/.local/share/giva/logs/`:

| File | Source | Mechanism |
|---|---|---|
| `server.log` / `server.err` | Python daemon | launchd stdout/stderr redirect |
| `giva-app.log` | SwiftUI app | `FileLogger` (dual: os.Logger + file) |

```bash
# Tail both during development
tail -f ~/.local/share/giva/logs/server.log ~/.local/share/giva/logs/giva-app.log
```

**Python**: `log = logging.getLogger(__name__)` at module level. Level controlled by `config.log_level` (default: `INFO`, override: `GIVA_LOG_LEVEL` env var). Never use `print()` for diagnostics.

**Swift**: `private let log = Log.make(category: "YourCategory")` at file level. Writes to both `os.Logger` (Console.app / `log stream`) and `~/.local/share/giva/logs/giva-app.log`. Categories: `Session`, `Bootstrap`, `Audio`. Level controlled by `GIVA_LOG_LEVEL` env var (default: `INFO`). Never use `print()` for diagnostics.

**Log levels**: `debug` for per-event/per-token noise; `info` for lifecycle transitions, state changes, and key decisions; `warning` for recoverable problems; `error` for failures that affect the user.

## Debugging Policy

- **Don't guess — instrument and debug.** When a bug is reported, add targeted logging/tracing to the relevant code path, have the user reproduce, and analyze the actual output. Do not speculate about root causes or apply blind fixes.
