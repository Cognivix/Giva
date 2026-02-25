# Giva

**Generative Intelligent Virtual Assistant** — a macOS-native personal assistant that syncs your email and calendar from Apple Mail/Calendar, runs local LLM inference on Apple Silicon via [MLX](https://github.com/ml-explore/mlx), and helps you manage tasks, goals, and daily priorities.

All data stays on your device. No cloud APIs, no telemetry.

> **⚠️ Alpha Software** — Giva is under active development and not ready for production use. APIs, configuration formats, and database schemas may change without notice. Expect rough edges, incomplete features, and breaking changes between commits. Use at your own risk.

## Features

- **Email sync & classification** — JXA-based Apple Mail integration with LLM-powered filtering (headers-only sync, lazy body fetching)
- **Calendar sync** — EventKit (native) with AppleScript fallback
- **Local LLM inference** — Dual-model architecture: large assistant (Qwen3-30B-A3B) for queries, small filter (Qwen3-8B) for classification
- **Goal tracking** — Hierarchical goals with strategy generation, objective decomposition, and daily reviews
- **Task extraction** — Automatic task detection from emails and calendar events
- **Post-chat agents** — Intent detection, progress tracking, and preference learning after every conversation
- **Voice mode** — Optional TTS (Qwen3-TTS) and STT (Lightning Whisper) via mlx-audio
- **MCP integration** — Pluggable Model Context Protocol servers for filesystem, web fetch, iMessage, Notes, and more
- **Writing style profiling** — Learns your communication patterns from sent emails

## Interfaces

| Interface | Description |
|-----------|-------------|
| `giva` | Interactive CLI (prompt-toolkit + rich) |
| `giva-server` | REST API + SSE streaming on `127.0.0.1:7483` |
| **Giva.app** | SwiftUI menu bar app with chat, tasks, and goals |

## Requirements

- **macOS 26** (Tahoe) with **Apple Silicon** (M1 or later)
- **Python 3.13+**
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
  cli.py            # Interactive REPL
  server.py         # FastAPI REST + SSE API
  config.py         # TOML config with env overrides
  db/               # SQLite + FTS5 data layer (WAL mode)
  sync/             # Apple Mail (JXA) + Calendar (EventKit/AppleScript)
  llm/              # MLX model management, prompts, structured output
  intelligence/     # Query handling, agents, goals, context assembly
  utils/            # AppleScript helpers, MIME parsing

GivaApp/            # SwiftUI macOS menu bar app
  Services/         # API client, bootstrap, server manager, logging
  Views/            # Chat, tasks, goals, bootstrap UI
  ViewModels/       # @Observable state management
  Models/           # Codable structs, ServerPhase enum
```

### Key Design Decisions

- **Local-only** — all data in SQLite at `~/.local/share/giva/giva.db`
- **Dual LLM** — assistant model for reasoning, filter model for high-frequency classification
- **Lazy email bodies** — sync fetches headers; bodies fetched on-demand when the LLM needs them
- **Budget-aware context** — token budget scales with model size (system 5%, query 5%, conversation 25%, retrieved 55%, headroom 10%)
- **Server-side state machine** — `ServerPhase` enum is the single source of truth; the SwiftUI app is a thin observer
- **Post-chat agent pipeline** — intent detection, task creation, and preference learning run automatically after every chat turn using the filter model

## Development

```bash
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

### Project Structure

- Python tests: `tests/` (mirrors `src/` structure)
- Swift tests: `GivaApp/GivaAppTests/` (Swift Testing framework)
- Isolated test DBs via `tmp_path` fixtures — no real LLM or Apple Mail calls in tests

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

Proprietary. All rights reserved.
