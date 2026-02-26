# G.I.V.A.

**Generative Intelligence Virtual Assistant** — a macOS-native personal assistant that syncs your email and calendar from Apple Mail/Calendar, runs local LLM inference on Apple Silicon via [MLX](https://github.com/ml-explore/mlx), and helps you manage tasks, goals, and daily priorities.

All data stays on your device. No cloud APIs, no telemetry.

> **⚠️ Alpha Software** — Giva is under active development and not ready for production use. APIs, configuration formats, and database schemas may change without notice. Expect rough edges, incomplete features, and breaking changes between commits. Use at your own risk.

## Features

- **Email sync & classification** — JXA-based Apple Mail integration with LLM-powered filtering (headers-only sync, lazy body fetching)
- **Calendar sync** — EventKit (native) with AppleScript fallback
- **Local LLM inference** — Dual-model architecture: large assistant (Qwen3-30B-A3B) for queries, small filter (Qwen3-8B) for classification. Models auto-recommended based on your hardware (chip, RAM, GPU cores) with live benchmark data
- **Goal tracking** — Hierarchical goals (long-term → mid-term → short-term) with strategy generation, objective decomposition, and daily reviews with reflection
- **Task extraction & review** — Automatic task detection from emails, events, and chat. Background review pipeline: sanity checks (expired deadlines, answered emails, past events), semantic dedup, 5-way classification (autonomous, needs input, user-only, project, dismiss), and intelligent routing. Learns from user dismissal patterns and caches review memory for future cycles
- **Pluggable agent framework** — Extensible agent system with protocol-based discovery, two-stage routing (keyword pre-filter → LLM classification), and a thread-safe priority queue. Built-in agents: orchestrator (multi-step planning), email drafter, and MCP server wrappers
- **Post-chat agents** — Intent detection, task creation, progress tracking, conversation tagging, and preference learning run automatically after every chat turn using the filter model
- **Three-tier conversation memory** — Active window (recent turns), session summary (compressed by filter model), and learned facts (permanent preferences extracted during daily review)
- **Proactive suggestions** — Morning briefing, priority engine, and upcoming event summaries
- **Voice mode** — Optional TTS (Qwen3-TTS) and STT (Lightning Whisper) via mlx-audio, with two-tier silence detection and progressive chunk transcription
- **MCP integration** — Pluggable Model Context Protocol servers for filesystem, web fetch, iMessage, Notes, Discord, and more. Servers auto-register as agents at startup
- **Writing style profiling** — Learns your communication patterns from sent emails
- **Power-aware scheduling** — Sync and model loading defer when on battery (≤50%), under thermal pressure, or high memory usage

## Interfaces

| Interface | Description |
|-----------|-------------|
| `giva` | Interactive CLI (prompt-toolkit + rich) |
| `giva-server` | REST API + SSE streaming on `127.0.0.1:7483` |
| **Giva.app** | SwiftUI menu bar panel + full window with chat, tasks, goals, agent activity, and settings (⌘,) |

## Requirements

- **macOS 26** (Tahoe) with **Apple Silicon** (M1 or later)
- **Python 3.11+**
- **Xcode 26+**
- **Node.js 18+** (for MCP servers)
- ~16 GB RAM recommended (for 30B assistant model)

## Quick Start

### Prerequisites

You need a Mac with **Apple Silicon** (M1 or later) running **macOS 26 Tahoe** or newer, and at least **16 GB RAM** (for the 30B assistant model).

**1. Install Xcode**

Install Xcode 26+ from the [Mac App Store](https://apps.apple.com/app/xcode/id497799835). After installation:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

**2. Install Homebrew**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

After installation, follow the instructions printed by Homebrew to add it to your PATH (typically adding an `eval` line to `~/.zprofile`).

**3. Install Python 3.13+**

```bash
brew install python@3.13
```

Verify:

```bash
python3 --version   # Should show 3.13 or later
```

**4. Install Node.js**

```bash
brew install node
```

### Install & Run

```bash
# Clone the repository
git clone https://github.com/Cognivix/Giva.git
cd Giva

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install with all features (dev tools, voice, MCP servers)
pip install -e ".[dev,voice,mcp]"

# Run the CLI
giva

# Or start the API server
giva-server
```

On first run, Giva downloads the default LLM models (~4 GB for assistant, ~2 GB for filter) and walks you through onboarding.

### SwiftUI App

Open `GivaApp/GivaApp.xcodeproj` in Xcode, build and run (⌘R). The app handles everything automatically:

1. Creates a Python venv at `~/.local/share/giva/.venv`
2. Pip-installs the project into it
3. Registers a `com.giva.server` launchd user agent for the API daemon
4. Connects to the daemon via health polling and SSE

> **Note:** If you're only using the SwiftUI app, you don't need to create a venv or run `pip install` manually — the app's bootstrap does this for you.

## Configuration

Configuration layers (later overrides earlier):

1. `config.default.toml` — shipped defaults
2. `~/.config/giva/config.toml` — user overrides
3. `GIVA_*` environment variables

Key environment overrides:

| Variable | Description |
|----------|-------------|
| `GIVA_LLM_MODEL` | Assistant model (e.g., `mlx-community/Qwen3-30B-A3B-4bit`) |
| `GIVA_LLM_FILTER_MODEL` | Filter model for email classification |
| `GIVA_DATA_DIR` | Data directory (default: `~/.local/share/giva`) |
| `GIVA_LOG_LEVEL` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Architecture

```
src/giva/
├── cli.py              # Interactive REPL (prompt-toolkit + rich)
├── server.py           # FastAPI REST + SSE API on 127.0.0.1:7483
├── config.py           # TOML config: config.default.toml → user → env
├── bootstrap.py        # Server-side bootstrap state machine
├── models.py           # HuggingFace model discovery + recommendation
├── hardware.py         # Mac hardware detection (chip, RAM, GPU cores)
├── benchmarks.py       # Live LLM benchmark fetching
├── db/
│   ├── models.py       # Dataclasses: Email, Event, Task, Goal, etc.
│   ├── store.py        # SQLite + FTS5 data layer (WAL mode)
│   └── migrations.py   # Schema versioning + ALTER migrations
├── sync/
│   ├── mail.py         # Apple Mail sync via JXA + LLM filter
│   ├── calendar.py     # EventKit or AppleScript fallback
│   └── scheduler.py    # Background sync via threading.Timer
├── llm/
│   ├── engine.py       # MLX dual-model: assistant + filter
│   ├── prompts.py      # All prompt templates
│   └── structured.py   # Pydantic models for structured output
├── intelligence/
│   ├── queries.py      # NL query → FTS5 → streamed LLM response
│   ├── tasks.py        # Task extraction from emails/events
│   ├── profile.py      # User profile analytics
│   ├── proactive.py    # Priority suggestion engine
│   ├── onboarding.py   # Conversational onboarding
│   ├── goals.py        # Goal CRUD + strategy generation
│   ├── agents.py       # Post-chat agent pipeline
│   ├── context.py      # Budget-aware context assembly
│   ├── daily_review.py # Daily goal review + reflection
│   ├── task_review.py  # Post-extraction task dedup, classify, route
│   └── mcp_observations.py # MCP source observations
├── agents/
│   ├── base.py         # Agent Protocol + AgentManifest
│   ├── registry.py     # Auto-discovery of giva.agents.*
│   ├── router.py       # Keyword pre-filter → LLM classification
│   ├── queue.py        # Thread-safe priority queue + SSE
│   ├── orchestrator/   # Plan → validate → execute → QA
│   ├── email_drafter/  # Email drafting with history context
│   └── mcp_agent/      # MCP server wrappers (no LLM)
├── audio/
│   ├── tts.py          # Qwen3-TTS via mlx-audio
│   ├── stt.py          # Lightning Whisper MLX
│   └── player.py       # Threaded audio playback queue
└── utils/
    ├── applescript.py   # osascript/JXA runner helpers
    ├── email_parser.py  # MIME parsing utilities
    ├── power.py         # Battery, thermal, memory monitoring
    └── recents.py       # Spotlight-based file discovery

GivaApp/                        # SwiftUI macOS app (Xcode project)
├── GivaApp.swift               # App entry point
├── Models/
│   └── APIModels.swift         # Codable structs + ServerPhase enum
├── Services/
│   ├── APIService.swift        # URLSession + SSE streaming
│   ├── APIServiceProtocol.swift # Protocol for DI + testing
│   ├── AgentActionHandler.swift # Shared agent action parsing
│   ├── BootstrapManager.swift  # First-run setup + launchd daemon
│   ├── ServerManager.swift     # Daemon health polling
│   ├── FileLogger.swift        # os.Logger + file logging
│   ├── AudioPlaybackService.swift  # AVFoundation playback
│   └── VoiceRecordingService.swift # AVAudioEngine recording
├── ViewModels/
│   ├── GivaViewModel.swift     # Central @Observable state
│   └── GoalsViewModel.swift    # Goals window state
├── Views/
│   ├── BootstrapView.swift     # First-run cooking spinner
│   ├── ModelSetupView.swift    # Model selection wizard
│   ├── GivaMainWindowView.swift # Full window: NavigationSplitView
│   ├── MainPanelView.swift     # Menu bar panel
│   ├── ChatView.swift          # Chat + Markdown rendering
│   ├── TaskListView.swift      # Task list + priority indicators
│   ├── TaskChatView.swift      # Task-scoped AI chat
│   ├── GoalsWindowView.swift   # Goals detail window
│   ├── SettingsView.swift      # Settings (⌘,): tabbed layout
│   ├── QuickActionsView.swift  # Bottom action bar
│   ├── AgentActivityPanel.swift    # Agent queue inspector
│   └── AgentConfirmationCard.swift # Agent approval card
└── GivaAppTests/               # Swift Testing suite

tests/                  # pytest suite mirroring src/ structure
scripts/                # Bootstrap + demo scripts
docs/                   # Agent architecture + bootstrap design
```

### Key Design Decisions

- **Local-only** — all data in SQLite at `~/.local/share/giva/giva.db`
- **Dual LLM** — assistant model (30B+) for reasoning and synthesis; filter model (≤8B) for high-frequency classification, extraction, and structured JSON
- **Lazy email bodies** — sync fetches headers; bodies fetched on-demand when the LLM needs them
- **Budget-aware context** — token budget scales with model size (system 5%, query 5%, conversation 25%, retrieved 55%, headroom 10%). Auto-scales: ≤1B→2K, ≤8B→4K, ≤32B→8K, >32B→12K tokens
- **Three-tier conversation memory** — Tier 1: active window (recent turns). Tier 2: session summary (compressed by filter model, resets daily). Tier 3: learned facts (permanent preferences, always in system prompt)
- **Server-side state machine** — `ServerPhase` checkpoint is the single source of truth (unknown → downloading → awaiting_model_selection → validating → ready → syncing → onboarding → operational). The SwiftUI app is a thin observer, never drives transitions
- **Post-chat agent pipeline** — intent detection, task creation, progress tracking, and preference learning run automatically after every chat turn using the filter model. Created tasks preserve links to the source email/event from the conversation context
- **Pluggable agents** — protocol-based discovery with two-stage routing. New agents register by dropping a module into `giva/agents/`. Filter model for classification agents, assistant model for synthesis agents
- **Power-aware scheduling** — sync defers on low battery (≤50%) or thermal pressure (≥ serious). Models auto-unload after configurable idle timeout
- **Goal-scoped conversations** — conversations table has nullable `goal_id`; global and goal chat are cleanly separated in the DB and UI

## Development

```bash
# Install with dev deps
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=giva --cov-report=term-missing

# Lint
ruff check src/ tests/

# Swift tests
xcodebuild test -project GivaApp/GivaApp.xcodeproj -scheme GivaApp \
  -destination 'platform=macOS' -only-testing:GivaAppTests
```

### Testing

- **Python tests** (`tests/`): mirrors `src/` structure. Uses `tmp_path` fixtures for isolated SQLite DBs — no real LLM or Apple Mail calls in tests
- **Swift tests** (`GivaApp/GivaAppTests/`): Swift Testing framework (`@Test`, `#expect`, `@Suite`). Uses `MockAPIService` conforming to `APIServiceProtocol` for dependency injection

### Adding New Agents

1. Create a module under `src/giva/agents/` (e.g., `my_agent/agent.py`)
2. Implement the `Agent` protocol from `giva.agents.base`
3. Export `AGENT_CLASS` or `agent_factory()` from the module
4. The `AgentRegistry` auto-discovers it at startup
5. Set `model_tier = "filter"` unless the agent needs reasoning/synthesis

## Logging

All logs in `~/.local/share/giva/logs/`:

| File | Source |
|------|--------|
| `server.log` / `server.err` | Python daemon (launchd stdout/stderr) |
| `giva-app.log` | SwiftUI app (FileLogger) |

```bash
# Tail both during development
tail -f ~/.local/share/giva/logs/server.log ~/.local/share/giva/logs/giva-app.log
```

## Lighter Install

The default `pip install -e ".[dev,voice,mcp]"` installs everything. If you want a smaller footprint:

| Install command | What you get | What you skip |
|-----------------|--------------|---------------|
| `pip install -e ".[dev,mcp]"` | CLI, API, MCP servers | Voice (TTS/STT) |
| `pip install -e ".[dev,voice]"` | CLI, API, voice | MCP servers (Node.js not needed) |
| `pip install -e ".[dev]"` | CLI + API only | Voice and MCP |

### Voice Mode

Included in the full install. Set `voice.enabled = true` in config to activate local TTS (Qwen3-TTS-0.6B) and STT (Lightning Whisper MLX).

### MCP Servers

Included in the full install. Configure servers in `~/.config/giva/config.toml`. Servers auto-register as agents at startup. See `config.default.toml` for examples (filesystem, web fetch, iMessage, Notes, Discord).

Servers that require API tokens (e.g., Discord) use secret references. Copy the template and fill in your values:

```bash
cp secrets.example.toml ~/.config/giva/secrets.toml
# Edit ~/.config/giva/secrets.toml with your tokens
```

In your config, reference secrets with a `$` prefix (e.g., `DISCORD_BOT_TOKEN = "$DISCORD_BOT_TOKEN"`). Missing secrets are logged as warnings and the server starts without them.

## License

Apache 2.0
