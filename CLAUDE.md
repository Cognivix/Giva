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
├── bootstrap.py        # Server-side bootstrap state machine (checkpoint Markov chain)
├── models.py           # HuggingFace model discovery, recommendation, download management
├── hardware.py         # Mac hardware detection (chip, RAM, GPU cores) for model sizing
├── benchmarks.py       # Live LLM benchmark fetching (Open LLM Leaderboard, LMArena)
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
│   ├── daily_review.py # Daily goal review + reflection
│   └── mcp_observations.py # MCP source observation gathering for onboarding/context
├── agents/
│   ├── base.py         # Agent Protocol, AgentManifest, AgentResult, BaseAgent
│   ├── registry.py     # AgentRegistry: auto-discovers agents in giva.agents.*
│   ├── router.py       # Two-stage routing: keyword pre-filter → LLM classification
│   ├── queue.py        # Thread-safe priority queue for background agent jobs
│   ├── orchestrator/
│   │   ├── agent.py    # OrchestratorAgent: plan → validate → execute → QA → synthesize
│   │   ├── planner.py  # Assistant-model plan generation + structural validation
│   │   ├── executor.py # Sequential subtask execution with QA checks
│   │   └── prompts.py  # Orchestrator-specific prompt templates
│   ├── email_drafter/
│   │   ├── agent.py    # EmailDrafterAgent: drafts emails using assistant model + history
│   │   └── prompts.py  # Email drafting prompt templates
│   └── mcp_agent/
│       ├── agent.py    # MCPAgent: wraps MCP servers as Giva agents (no LLM)
│       ├── config.py   # MCPServerConfig: parsed MCP server definitions
│       ├── lifecycle.py # MCPConnection: stdio/SSE process management
│       └── _compat.py  # MCP SDK version compatibility shim
├── audio/
│   ├── tts.py          # Qwen3-TTS via mlx-audio, per-sentence synthesis
│   ├── stt.py          # Lightning Whisper MLX speech-to-text
│   └── player.py       # Threaded audio playback queue for streaming TTS
└── utils/
    ├── applescript.py   # osascript/JXA runner helpers
    ├── email_parser.py  # MIME parsing utilities
    ├── power.py         # Battery, thermal, memory pressure monitoring (cached)
    └── recents.py       # Spotlight-based recently-used file discovery

GivaApp/                        # SwiftUI macOS app (Xcode project)
├── GivaApp.swift               # App entry point (bootstrap → main UI)
├── Models/
│   └── APIModels.swift         # Codable structs + ServerPhase enum
├── Services/
│   ├── APIService.swift        # URLSession wrapper + SSE streaming
│   ├── APIServiceProtocol.swift # Protocol for dependency injection + testing
│   ├── AgentActionHandler.swift # Shared agent action parsing (used by both ViewModels)
│   ├── BootstrapManager.swift  # First-run: venv creation, pip install, launchd daemon
│   ├── ServerManager.swift     # Connects to launchd-managed daemon (health polling)
│   ├── FileLogger.swift        # Dual-destination logger: os.Logger + file
│   ├── AudioPlaybackService.swift  # AVFoundation playback queue for TTS audio chunks
│   └── VoiceRecordingService.swift # AVAudioEngine recording + two-tier silence detection
├── ViewModels/
│   ├── GivaViewModel.swift     # Central state management (@Observable)
│   └── GoalsViewModel.swift    # Goals window state (@Observable)
├── Views/
│   ├── BootstrapView.swift     # Cooking spinner shown during first-run setup
│   ├── ModelSetupView.swift    # Model selection wizard (awaiting_model_selection phase)
│   ├── GivaMainWindowView.swift # Full-app window: NavigationSplitView with sidebar
│   ├── MainPanelView.swift     # Menu bar panel: header + tabs + content + quick actions
│   ├── ChatView.swift          # Chat messages + input + MarkdownText renderer
│   ├── TaskListView.swift      # Task list with priority indicators
│   ├── TaskChatView.swift      # Task-scoped contextual AI chat
│   ├── GoalsWindowView.swift   # Goals detail window (strategy, objectives, goal chat)
│   ├── SettingsView.swift      # Settings window (⌘,): Models, Sync, General, Goals, Profile
│   ├── QuickActionsView.swift  # Bottom action bar
│   ├── AgentActivityPanel.swift    # Inspector panel: agent queue status
│   └── AgentConfirmationCard.swift # Inline agent approval/dismissal card
└── GivaAppTests/               # Swift Testing test suite
    ├── Mocks/
    │   └── MockAPIService.swift    # Configurable mock (stubs + call counting)
    ├── APIModelsTests.swift
    ├── ServerPhaseTests.swift
    ├── AgentActionHandlerTests.swift
    ├── ServerManagerTests.swift
    ├── GivaViewModelTests.swift
    └── GoalsViewModelTests.swift

tests/                  # pytest test suite mirroring src/ structure
├── conftest.py         # Shared fixtures: tmp_db, config
├── test_cli.py, test_server.py, test_server_extended.py
├── test_models.py, test_benchmarks.py, test_hardware.py
├── test_db/            # store, config, migrations, models, goals
├── test_intelligence/  # agents, context, goals, onboarding, profile, queries, tasks, mcp_observations
├── test_agents/        # base, registry, router, queue, orchestrator, email_drafter, mcp_agent
├── test_sync/          # mail_sync, calendar, filter, scheduler
├── test_audio/         # tts, stt
├── test_llm/           # prompts, structured
└── test_utils/         # applescript, email_parser, recents

scripts/                # One-shot utility scripts
├── giva-setup.py       # Bootstrap script: venv, pip install, launchd plist (emits JSON progress)
└── sync_and_prioritize.py  # Demo/debug: sync → LLM prioritization pipeline

docs/
├── agent-architecture.md   # Full agent design: routing tables, memory tiers, model assignment
└── bootstrap-design.md     # Bootstrap lifecycle, model download workflow, daemon management
```

## Topic-Based Taxonomy

Cross-cutting map of files grouped by functional concern, regardless of where they
live in the directory tree. Use this to find *all* relevant files for a given topic.

### 1. LLM Inference & Prompting
The local LLM pipeline: model loading, prompt construction, structured output parsing.

| File | Role |
|---|---|
| `src/giva/llm/engine.py` | MLX ModelManager: lazy dual-model loading, generate/stream, idle unload |
| `src/giva/llm/prompts.py` | Every prompt template (chat, filter, onboarding, agents, goals, etc.) |
| `src/giva/llm/structured.py` | Pydantic models for typed LLM output (intents, plans, profile updates) |
| `src/giva/intelligence/context.py` | Budget-aware context assembly: slot allocation, conversation memory tiers |
| `config.default.toml` `[llm]` | Model IDs, max tokens, temperature, context budget |

### 2. Data Sync (Mail & Calendar)
Ingestion of email and calendar data from Apple system apps into SQLite.

| File | Role |
|---|---|
| `src/giva/sync/mail.py` | Apple Mail sync via JXA: chunked headers, on-demand body fetch, LLM filter |
| `src/giva/sync/calendar.py` | EventKit (fast) or AppleScript (fallback) calendar sync |
| `src/giva/sync/scheduler.py` | Background sync scheduling via `threading.Timer`, power-aware |
| `src/giva/utils/applescript.py` | `osascript`/JXA runner helpers (used by mail + calendar) |
| `src/giva/utils/email_parser.py` | MIME parsing utilities for email body extraction |
| `config.default.toml` `[mail]`, `[calendar]` | Sync intervals, batch sizes, history windows |

### 3. Persistence & Data Models
SQLite storage, schema management, and data type definitions.

| File | Role |
|---|---|
| `src/giva/db/store.py` | SQLite + FTS5 data layer (WAL mode, SCHEMA_SQL constant, schema v4) |
| `src/giva/db/models.py` | Dataclasses: Email, Event, Task, UserProfile, Goal, GoalStrategy |
| `src/giva/db/migrations.py` | Schema version detection + ALTER TABLE migrations |
| `src/giva/config.py` | TOML config loading: default → user → env overlay |
| `config.default.toml` | Full default configuration file |

### 4. Intelligence Layer
High-level AI features built on top of the LLM + data layers.

| File | Role |
|---|---|
| `src/giva/intelligence/queries.py` | NL query → FTS5 retrieval → context assembly → streamed LLM response |
| `src/giva/intelligence/tasks.py` | Task extraction from emails/events via LLM |
| `src/giva/intelligence/profile.py` | SQL-first user profile analytics + LLM topic extraction |
| `src/giva/intelligence/proactive.py` | Priority suggestion engine (morning briefing, upcoming events) |
| `src/giva/intelligence/onboarding.py` | Multi-step conversational onboarding with profile extraction |
| `src/giva/intelligence/goals.py` | Goal CRUD, strategy generation, progress tracking |
| `src/giva/intelligence/agents.py` | Post-chat agent pipeline: intent detection + action routing (filter model) |
| `src/giva/intelligence/context.py` | Budget-aware context assembly + 3-tier conversation memory |
| `src/giva/intelligence/daily_review.py` | Daily goal review, reflection, fact extraction to Tier 3 |
| `src/giva/intelligence/mcp_observations.py` | Gathers observations from MCP sources (Notes, Messages, Discord) |

### 5. Pluggable Agent Framework
The extensible agent system: protocol, discovery, routing, execution queue, and built-in agents.

| File | Role |
|---|---|
| `src/giva/agents/base.py` | `Agent` Protocol, `AgentManifest`, `AgentResult`, `BaseAgent` helpers |
| `src/giva/agents/registry.py` | `AgentRegistry`: auto-discovers `giva.agents.*` sub-modules |
| `src/giva/agents/router.py` | Two-stage routing: keyword pre-filter → LLM classification (filter model) |
| `src/giva/agents/queue.py` | `AgentQueue`: thread-safe priority queue, SSE broadcast, confirmation flow |
| `src/giva/agents/orchestrator/` | Meta-agent: plan → validate → execute sub-agents → QA → synthesize |
| `src/giva/agents/email_drafter/` | Drafts emails using assistant model + email history context |
| `src/giva/agents/mcp_agent/` | Wraps MCP servers (filesystem, fetch, iMessage, Notes, Discord) as agents |
| `GivaApp/Services/AgentActionHandler.swift` | Swift-side: parses agent actions, confirmations, queued agent names |
| `GivaApp/Views/AgentActivityPanel.swift` | UI: agent queue status inspector panel |
| `GivaApp/Views/AgentConfirmationCard.swift` | UI: inline agent approval/dismissal card |

### 6. Bootstrap & Server Lifecycle
First-run setup, model download, daemon management, and the phase state machine.

| File | Role |
|---|---|
| `src/giva/bootstrap.py` | Server-side bootstrap state machine (Markov chain of checkpoints) |
| `src/giva/models.py` | HuggingFace model discovery, LLM-based recommendation, download tracking |
| `src/giva/hardware.py` | Mac hardware detection (chip, RAM, GPU) for model sizing |
| `src/giva/benchmarks.py` | Live benchmark data fetching for model recommendation |
| `scripts/giva-setup.py` | One-shot bootstrap: venv creation, pip install, launchd plist |
| `GivaApp/Services/BootstrapManager.swift` | Swift-side: venv setup, pip install, launchd agent registration |
| `GivaApp/Services/ServerManager.swift` | Daemon health polling, connection lifecycle |
| `GivaApp/Views/BootstrapView.swift` | Cooking spinner during first-run setup |
| `GivaApp/Views/ModelSetupView.swift` | Model selection wizard (awaiting_model_selection phase) |
| `GivaApp/Models/APIModels.swift` | `ServerPhase` enum — single source of truth for UI state |
| `docs/bootstrap-design.md` | Full bootstrap lifecycle design doc |

### 7. Server & API
The FastAPI HTTP layer that bridges the Python backend to the Swift frontend.

| File | Role |
|---|---|
| `src/giva/server.py` | FastAPI app: REST endpoints, SSE streaming, LLM/voice locks, lifespan |
| `src/giva/config.py` | Config loading (also served via `GET/PUT /api/config`) |
| `GivaApp/Services/APIService.swift` | URLSession HTTP client + SSE streaming parser |
| `GivaApp/Services/APIServiceProtocol.swift` | Protocol for mock injection in tests |
| `GivaApp/Models/APIModels.swift` | Codable request/response structs mirroring Python schemas |

### 8. CLI
The interactive terminal interface.

| File | Role |
|---|---|
| `src/giva/cli.py` | prompt-toolkit + rich REPL: chat, sync triggers, status display |

### 9. Voice / Audio
Text-to-speech, speech-to-text, and audio playback across Python and Swift.

| File | Role |
|---|---|
| `src/giva/audio/tts.py` | Qwen3-TTS via mlx-audio: per-sentence synthesis, streaming chunks |
| `src/giva/audio/stt.py` | Lightning Whisper MLX: mic recording + transcription |
| `src/giva/audio/player.py` | Python-side threaded audio playback queue |
| `src/giva/server.py` (`_voice_lock`) | Voice lock serializing TTS/STT calls (separate from `_llm_lock`) |
| `GivaApp/Services/AudioPlaybackService.swift` | AVFoundation playback of base64-encoded WAV chunks |
| `GivaApp/Services/VoiceRecordingService.swift` | AVAudioEngine recording, two-tier silence detection, progressive SSE STT |
| `config.default.toml` `[voice]` | TTS model, STT model, voice name, sample rate |

### 10. Hardware & Power Management
Resource-aware scheduling: battery, thermal, memory pressure, model idle unloading.

| File | Role |
|---|---|
| `src/giva/hardware.py` | Apple Silicon chip/RAM/GPU detection via `sysctl` |
| `src/giva/utils/power.py` | `PowerState`: battery, thermal, memory pressure (cached, 30s TTL) |
| `src/giva/sync/scheduler.py` | Power-aware sync deferral (battery ≤50%, thermal ≥ serious) |
| `src/giva/llm/engine.py` | Model idle unload after configurable timeout |
| `config.default.toml` `[power]` | Battery/thermal thresholds, idle unload timeout |

### 11. SwiftUI App (Views & ViewModels)
The macOS frontend: menu bar panel, full window, and all UI views.

| File | Role |
|---|---|
| `GivaApp/GivaApp.swift` | App entry point: menu bar + full window scenes |
| `GivaApp/ViewModels/GivaViewModel.swift` | Central `@Observable` state: phases, chat, tasks, agents, settings |
| `GivaApp/ViewModels/GoalsViewModel.swift` | Goals window `@Observable` state: strategies, objectives, goal chat |
| `GivaApp/Views/GivaMainWindowView.swift` | Full window: `NavigationSplitView` (sidebar/content/inspector) |
| `GivaApp/Views/MainPanelView.swift` | Menu bar panel: header + tabs + content + quick actions |
| `GivaApp/Views/ChatView.swift` | Chat messages + input + Markdown renderer |
| `GivaApp/Views/TaskListView.swift` | Task list with priority indicators |
| `GivaApp/Views/TaskChatView.swift` | Task-scoped contextual AI chat |
| `GivaApp/Views/GoalsWindowView.swift` | Goals: strategy, objectives, goal-scoped chat |
| `GivaApp/Views/SettingsView.swift` | Settings window (⌘,): Models, Sync, General, Goals, Profile tabs |
| `GivaApp/Views/QuickActionsView.swift` | Bottom action bar (sync, voice, reset) |

### 12. Testing
Test infrastructure across both Python and Swift.

| File | Role |
|---|---|
| `tests/conftest.py` | Shared pytest fixtures: `tmp_db`, `config` (isolated temp dirs) |
| `tests/test_db/` | Store, config, migrations, models, goals (7 files) |
| `tests/test_intelligence/` | Agents, context, goals, onboarding, profile, queries, tasks (11 files) |
| `tests/test_agents/` | Base, registry, router, queue, orchestrator, email_drafter, MCP (12 files) |
| `tests/test_sync/` | Mail sync, calendar, filter, scheduler (5 files) |
| `tests/test_audio/` | TTS, STT (2 files) |
| `tests/test_llm/` | Prompts, structured output (2 files) |
| `tests/test_utils/` | AppleScript, email parser, recents (3 files) |
| `tests/test_server.py`, `test_server_extended.py` | API endpoint tests |
| `tests/test_cli.py` | CLI interaction tests |
| `tests/test_models.py`, `test_benchmarks.py`, `test_hardware.py` | Model/hardware tests |
| `GivaAppTests/Mocks/MockAPIService.swift` | Configurable mock with stubs + call counting |
| `GivaAppTests/*.swift` | Swift Testing suite: ViewModel, ServerPhase, AgentAction, ServerManager |

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

### Python
```bash
pytest                       # all tests
pytest tests/test_db/        # DB layer only
pytest -x -q                 # stop on first failure, quiet
```

Tests use `tmp_path` fixtures for isolated SQLite DBs. No real LLM or Apple Mail calls in tests.

### Swift (GivaAppTests)
```bash
xcodebuild test -project GivaApp/GivaApp.xcodeproj -scheme GivaApp \
  -destination 'platform=macOS' -only-testing:GivaAppTests
```

Uses the **Swift Testing** framework (`@Test`, `#expect`, `@Suite`). Test target is a hosted unit test bundle (`TEST_HOST` = app binary). Mock API via `MockAPIService` conforming to `APIServiceProtocol`.

## Conventions

- Python 3.13+, ruff line-length 100
- `src/` layout with `setuptools`
- Frozen dataclasses for config, mutable for DB models
- All LLM prompt templates live in `src/giva/llm/prompts.py`
- Structured LLM output uses Pydantic models in `src/giva/llm/structured.py`
- DB schema is in `Store.SCHEMA_SQL` constant (not separate SQL files)
- **Prompt design**: chat prompts enforce brevity and reference background agents. Onboarding prompts require visible text BEFORE any `<profile_update>` tag block.
- **Agent design**: new agents use the filter model unless they need reasoning/synthesis. Post-chat agents are batched into a single LLM call to minimize lock contention. See `docs/agent-architecture.md` for routing tables.
- **SwiftUI UI**: Apple HIG — progressive disclosure (system actions in gear menu, daily actions in bottom bar), content-first layout, `serverPhase` as single source of truth. Full guidelines in `docs/agent-architecture.md` § 7.
- **`@Observable` pattern (macOS 26+)**: All ViewModels and managers use `@Observable` (not `ObservableObject`/`@Published`). Owned objects use `@State` (not `@StateObject`). Environment injection uses `.environment(obj)` / `@Environment(Type.self)` (not `.environmentObject` / `@EnvironmentObject`). For two-way bindings on `@Observable` objects, use `@Bindable var viewModel = viewModel` as a local variable inside `body` or `@ViewBuilder` computed properties.
- **`ServerPhase` enum**: All server phase comparisons use the `ServerPhase` enum (in `APIModels.swift`), never raw strings. Convert from server strings via `ServerPhase(serverString:)`.
- **`APIServiceProtocol`**: All ViewModels reference `any APIServiceProtocol`, never concrete `APIService`. This enables mock injection for testing. Default parameter values are provided via protocol extension.
- **`AgentActionHandler`**: Shared agent action parsing (actions, confirmations, queued agent names) lives in `AgentActionHandler.swift`. Both `GivaViewModel` and `GoalsViewModel` use it — never duplicate parsing logic.
- **No system dialogs in menu bar apps**: `.confirmationDialog`, `.alert`, and `.sheet` do not work reliably inside `MenuBarExtra(.window)` popovers — they appear behind the popover, fail to dismiss, or never show at all. **Always use inline confirmation banners** embedded in the view hierarchy instead. See `MainPanelView.confirmationBanner(for:)` for the pattern.
- **Xcode project file**: When adding or removing `.swift` files outside of Xcode, you **must** manually update `GivaApp.xcodeproj/project.pbxproj`. Each new file requires entries in four places: (1) `PBXBuildFile` — a build reference pointing to the file reference, (2) `PBXFileReference` — the file's identity and type, (3) `PBXGroup` — add the file reference to its parent group's `children` list, (4) `PBXSourcesBuildPhase` — add the build reference to the `files` list. App source files use `A1xxxxxx`/`A2xxxxxx` IDs; test files use `B1xxxxxx`/`B2xxxxxx`. Forgetting any of these causes "Cannot find X in scope" build errors.

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

## Taxonomy Maintenance

The **Architecture** tree and **Topic-Based Taxonomy** sections above are the canonical map
of the codebase. They must stay accurate as the project evolves.

**When to update:** After any session that adds, removes, renames, or moves source files
(Python or Swift), update both sections before committing:

1. **Architecture tree** — add/remove the file entry with a one-line comment describing its role.
2. **Topic-Based Taxonomy** — add/remove the file in every relevant topic table. A single file
   may appear in multiple topics (e.g., `server.py` appears in both *Server & API* and
   *Voice / Audio* for the `_voice_lock`).
3. **Test counts** — if test files are added/removed, update the parenthetical counts in
   topic 12 (*Testing*).
4. **README.md** — as the final step, update `README.md` to reflect any changes. Keep the
   README's Architecture tree, Features list, and Key Design Decisions consistent with
   `CLAUDE.md`. The README is user-facing (concise, onboarding-oriented); `CLAUDE.md` is the
   authoritative reference (exhaustive, AI-session-oriented). Don't duplicate every detail —
   summarize new subsystems in Features, add new files to the Architecture tree, and add new
   design decisions only when they affect how users understand or configure the system.

**Rules:**
- Keep tree entries alphabetically sorted within each directory level.
- Keep topic table rows alphabetically sorted by file path.
- One-line comments in the tree should be ≤ 70 chars.
- Never remove a topic section — mark it `(empty)` if all files are deleted.
- When creating a new top-level package or a new cross-cutting concern, add both a tree
  section and a new numbered topic.
