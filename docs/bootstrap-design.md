# Bootstrap & Model Download — Design Document

## 1. Current System Assessment

### 1.1 High-Level Flow

```
App Launch
    │
    ▼
BootstrapManager.start()          ← ALL logic lives in SwiftUI app
    │
    ├── isBootstrapped? ──yes──► Fast Path: reconnect to daemon, check for upgrade
    │                                │
    │                                ├── commit mismatch? ──► upgrade() [nuke venv, re-bootstrap]
    │                                │
    │                                └── done ──► isComplete = true
    │
    └── no ──► runBootstrap()
                  │
                  ├── 1. findingPython       (Swift Process → /usr/bin/python3 --version)
                  ├── 2. resolveProjectRoot  (Swift FileManager walks up from bundle)
                  ├── 3. creatingVenv        (Swift Process → python3 -m venv)
                  ├── 4. installingDeps      (Swift Process → pip install -e ".[voice]")
                  ├── 5. downloadDefaultModel(Swift Process → python -c "snapshot_download()")
                  ├── 6. installingDaemon    (Swift writes plist + launchctl bootstrap)
                  └── 7. waitForHealth       (Swift polls /api/health for 90s)
                         │
                         ▼
              isComplete = true
                         │
                         ▼
           GivaViewModel.connectToServer()
                         │
                         ├── checkModelSetup()  ──► is config.toml missing [llm]?
                         │       │
                         │       ├── yes ──► ModelSetupView (SSE download with progress bars)
                         │       └── no  ──► MainPanelView
                         │
                         ├── loadProfile()
                         └── checkOnboarding()
```

### 1.2 What's Wrong With This Architecture

The SwiftUI app is the **controller and executor** of the entire bootstrap. This creates
fundamental problems:

1. **If the user quits the app mid-bootstrap, everything stops.** The download, pip install,
   venv creation — all are child processes of the app. They get orphaned or killed.

2. **If the user closes the popover, the UI is gone but the process continues invisibly.**
   Reopening shows stale or reset state.

3. **The app cannot observe a process it is also controlling.** Progress requires polling
   its own child process's side effects (disk size), which is fragile and duplicated.

4. **Two completely different code paths for model download.** Bootstrap uses a fire-and-forget
   `Process` with no progress. Model setup uses SSE with progress. Same operation, different
   implementations, different bugs.

5. **Recovery requires the app to be running.** If the daemon is fine but the app was killed
   mid-setup, the only recovery path goes through the app re-running the whole thing.

### 1.3 Controller Logic Currently in Swift (BootstrapManager.swift)

| Responsibility | Lines | Can move to server? |
|---|---|---|
| Find system Python 3.11+ | 220-279 | Yes — `giva-setup` script |
| Resolve project root (walk up from bundle) | 283-326 | Partially — needs bundle path from app |
| Create venv | 330-346 | Yes |
| pip install -e ".[voice]" | 350-364 | Yes |
| Download default model (snapshot_download) | 375-387 | Yes — already done server-side for model setup |
| Write launchd plist | 391-428 | Yes |
| launchctl bootstrap/bootout | 491-519 | **No** — must be the app or a helper |
| Wait for /api/health | 523-539 | No — this is the client's job |
| Dirty flag (UserDefaults) | 49, 161, 209 | Replaced by server-side checkpoint file |
| Git commit comparison | 98-151 | Yes — server already reports its commit |
| Upgrade (nuke venv + re-bootstrap) | 430-452 | Yes (except launchctl) |

**Key insight:** The only thing that *must* stay in Swift is `launchctl` calls (because
the daemon can't restart itself) and the initial "is there a venv at all?" check (because
the server doesn't exist yet). Everything else can and should run in a standalone Python
script that the daemon invokes.

---

## 2. Identified Bugs

### 2.1 CRITICAL: No Progress During Bootstrap Model Download

**Location:** `BootstrapManager.swift:375-387`

The default model download (~4 GB) runs via `runProcess()` which buffers all output until
exit. The user sees a static spinner for 5+ minutes with zero progress.

### 2.2 CRITICAL: Download Progress Size Mismatch

**Location:** `server.py:1017-1035` vs `models.py:631-644`

Total counts only `.safetensors/.bin/.gguf`, but downloaded counts ALL files.
Progress is erratic and can jump to 99.9% prematurely.

### 2.3 HIGH: `total_bytes` Can Be Zero

**Location:** `server.py:1002-1003, 1046`

If HuggingFace API fails, progress is permanently 0% with only MB count increasing.

### 2.4 HIGH: Retry Bypasses Dirty Flag Cleanup

**Location:** `BootstrapView.swift:60-64`

Retry calls `runBootstrap()` directly, skipping `start()` which does the `cleanVenv()`.

### 2.5 HIGH: Double `connectToServer()` After Upgrade

**Location:** `GivaApp.swift:40-42` + `GivaViewModel.swift:467-473`

Both `triggerUpgrade()` and `.onChange` observer call `connectToServer()` concurrently.

### 2.6 MEDIUM: No Download Cancellation

`selectAndDownloadModels()` launches an untracked `Task`. Multiple clicks = multiple
concurrent Tasks mutating the same state.

### 2.7 MEDIUM: Orphaned Processes on Quit

Quit during bootstrap orphans child processes (pip, python). Dirty flag handles recovery
but the zombie process can conflict with the next attempt.

### 2.8 MEDIUM: `is_model_setup_complete()` Is Fragile

Substring check (`"[llm]" in content and "model" in content`) instead of TOML parsing.
Doesn't verify models are actually downloaded.

### 2.9 LOW: Nuclear Auto-Upgrade

Commit mismatch → delete entire venv + re-download. Should try `pip install -e .` first.

---

## 3. Proposed Architecture: Server-Owned Bootstrap

### 3.1 Core Principle

**The daemon owns all setup state and long-running operations. The SwiftUI app is a pure
observer that can quit and restart at any time without affecting the process.**

```
┌─────────────────────────────────────────────────────┐
│                   SwiftUI App (Observer)             │
│                                                     │
│  On launch:                                         │
│    1. Is venv + giva-server installed?              │
│       NO  → run giva-setup (one-shot script)        │
│       YES → ensure launchd agent loaded             │
│    2. Poll GET /api/bootstrap/status (SSE or poll)  │
│    3. Render whatever the server reports             │
│    4. Can quit/reopen at ANY time                   │
│                                                     │
│  Sends commands:                                    │
│    POST /api/bootstrap/start                        │
│    POST /api/bootstrap/retry                        │
│    POST /api/models/select + /api/models/download   │
│    POST /api/upgrade                                │
│    POST /api/reset                                  │
│                                                     │
│  NEVER runs:                                        │
│    pip install, snapshot_download, venv creation,    │
│    model downloads, git checks, or any subprocess   │
│    except launchctl and giva-setup                  │
└─────────────────────────────────────────────────────┘
                          │
                    SSE / REST
                          │
┌─────────────────────────────────────────────────────┐
│              giva-server (FastAPI daemon)            │
│                                                     │
│  Owns:                                              │
│    - Bootstrap state machine (checkpoint file)      │
│    - Model downloads (with SSE progress)            │
│    - Self-upgrade (pip install, restart signal)      │
│    - All config validation                          │
│    - Health + readiness reporting                   │
│                                                     │
│  Persists state to:                                 │
│    ~/.local/share/giva/bootstrap.json               │
│                                                     │
│  Survives:                                          │
│    App quit, popover close, crash — launchd         │
│    restarts automatically                           │
└─────────────────────────────────────────────────────┘
                          │
                     launchd
                     (KeepAlive)
                          │
┌─────────────────────────────────────────────────────┐
│           giva-setup (one-shot script)              │
│                                                     │
│  Only runs when NO venv exists at all.              │
│  Minimal responsibilities:                          │
│    1. Find Python 3.11+                             │
│    2. Create venv                                   │
│    3. pip install giva                              │
│    4. Write launchd plist                           │
│  Then exits. App calls launchctl to start daemon.   │
│  Daemon takes over from there (model download etc). │
└─────────────────────────────────────────────────────┘
```

### 3.2 The Three-Layer Split

#### Layer 1: `giva-setup` (Python script, runs without venv)

A standalone script shipped inside the app bundle (or at a known path in the repo).
Runs using the **system Python**, not the venv. Its only job is to create the venv
and install giva so the daemon can start.

```
scripts/giva-setup.py
```

**Responsibilities:**
- Find Python 3.11+ (same search as current Swift code)
- Accept `--project-root <path>` from the app (the app knows its bundle location)
- Create `~/.local/share/giva/.venv`
- `pip install -e ".[voice]"` into the venv
- Write `~/Library/LaunchAgents/com.giva.server.plist`
- Write checkpoint: `~/.local/share/giva/bootstrap.json` → `{"checkpoint": "deps_installed"}`
- Print JSON progress to stdout for the app to read (simple, line-delimited):
  ```json
  {"step": "finding_python", "status": "running"}
  {"step": "finding_python", "status": "done", "detail": "/opt/homebrew/bin/python3"}
  {"step": "creating_venv", "status": "running"}
  ...
  {"step": "installing_deps", "status": "running", "detail": "pip install -e .[voice]"}
  {"step": "installing_deps", "status": "done"}
  {"step": "complete"}
  ```

**Does NOT:**
- Download models (that's the daemon's job)
- Start the daemon (the app does `launchctl bootstrap`)
- Manage any ongoing state

**Idempotent:** If the venv already exists and is healthy, skips to the end.
If partially created, validates what exists and resumes.

#### Layer 2: `giva-server` (FastAPI daemon, managed by launchd)

After the venv is created and the daemon starts, it **owns all remaining setup**:

New endpoints:
```
GET  /api/bootstrap/status     → current state + progress
POST /api/bootstrap/start      → trigger remaining setup (model download etc)
POST /api/bootstrap/retry      → retry from last failed step
SSE  /api/bootstrap/stream     → live progress stream
POST /api/upgrade              → self-upgrade (pip install + restart)
```

The daemon's startup sequence (in `lifespan`):
```python
async def lifespan(app):
    config = load_config()
    store = Store(config.db_path)
    app.state.config = config
    app.state.store = store

    # Initialize bootstrap state from checkpoint file
    app.state.bootstrap = BootstrapState.load()

    # If bootstrap isn't complete, auto-start remaining steps
    if not app.state.bootstrap.is_ready:
        asyncio.create_task(run_bootstrap_steps(app))

    yield
```

#### Layer 3: SwiftUI App (pure observer)

The app becomes thin:

```swift
@MainActor
class BootstrapManager: ObservableObject {
    @Published var status: BootstrapStatus?  // from server
    @Published var isServerReachable = false

    // The ONLY subprocess the app ever runs
    func ensureEnvironment() async {
        if isVenvHealthy() {
            ensureLaunchdAgent()
            await pollUntilHealthy()
        } else {
            await runSetupScript()   // giva-setup.py
            ensureLaunchdAgent()
            await pollUntilHealthy()
        }
    }

    // After server is healthy, just observe
    func observeBootstrap() -> AsyncThrowingStream<BootstrapStatus, Error> {
        apiService.sseStream(url: "/api/bootstrap/stream")
    }
}
```

### 3.3 Server-Side Bootstrap State Machine

```
┌──────────────────────────────────────────────────────────────────┐
│                    Bootstrap State Machine                        │
│                    (lives in giva-server)                         │
│                                                                  │
│  ┌─────────┐     ┌────────────────┐     ┌──────────────┐        │
│  │ venv_ok │────►│ downloading    │────►│ configuring  │        │
│  │         │     │ _default_model │     │ _models      │        │
│  └─────────┘     │                │     │              │        │
│                  │ progress: 0-100│     │ (user picks  │        │
│                  └───────┬────────┘     │  or defaults)│        │
│                          │              └──────┬───────┘        │
│                          │ fail                │                │
│                          ▼                     ▼                │
│                  ┌──────────┐          ┌──────────────┐         │
│                  │  failed  │          │ downloading  │         │
│                  │          │◄─────────│ _user_models │         │
│                  │ error_msg│  fail    │              │         │
│                  │ retry_at │          │ progress {}  │         │
│                  └──────────┘          └──────┬───────┘         │
│                       ▲                       │                 │
│                       │                       ▼                 │
│                       │                ┌─────────────┐          │
│                       │                │ validating  │          │
│                       │                │             │          │
│                       └────────────────│ (load test) │          │
│                              fail      └──────┬──────┘          │
│                                               │                 │
│                                               ▼                 │
│                                        ┌────────────┐           │
│                                        │   ready    │           │
│                                        │            │           │
│                                        │ models OK  │           │
│                                        │ config OK  │           │
│                                        └────────────┘           │
│                                               │                 │
│                                        upgrade / reset          │
│                                               │                 │
│                                        ┌────────────┐           │
│                                        │ upgrading  │           │
│                                        └────────────┘           │
└──────────────────────────────────────────────────────────────────┘
```

#### Checkpoint File: `~/.local/share/giva/bootstrap.json`

```json
{
  "version": 1,
  "checkpoint": "downloading_user_models",
  "steps_completed": [
    "venv_ok",
    "default_model_downloaded",
    "models_configured"
  ],
  "current_step": "downloading_user_models",
  "progress": {
    "mlx-community/Qwen3-30B-A3B-4bit": 67.3,
    "mlx-community/Qwen3-8B-4bit": 100.0
  },
  "error": null,
  "updated_at": "2026-02-22T14:30:00Z"
}
```

This file is the **single source of truth**. Both the server (on startup) and the app
(via the API) read it. The app never writes it.

### 3.4 Bootstrap Status API

#### `GET /api/bootstrap/status`

Returns the current snapshot:

```json
{
  "state": "downloading_user_models",
  "ready": false,
  "needs_user_input": false,
  "steps": [
    {"name": "venv_ok", "status": "done"},
    {"name": "default_model", "status": "done"},
    {"name": "model_config", "status": "done"},
    {"name": "user_models", "status": "running", "progress": {
      "mlx-community/Qwen3-30B-A3B-4bit": {
        "percent": 67.3,
        "downloaded_mb": 10248.5,
        "total_mb": 15230.0
      }
    }},
    {"name": "validation", "status": "pending"}
  ],
  "error": null
}
```

Special states that require UI interaction:
- `"needs_user_input": true` when `state == "configuring_models"` — the server has
  model recommendations ready and waits for the user to confirm via
  `POST /api/models/select`.
- `"state": "failed"` — shows error + retry button.

#### `GET /api/bootstrap/stream` (SSE)

Long-lived SSE connection. Emits events as state changes:

```
event: status
data: {"state": "downloading_default_model", "progress": {"percent": 45.2, ...}}

event: status
data: {"state": "downloading_default_model", "progress": {"percent": 46.1, ...}}

event: step_complete
data: {"step": "default_model"}

event: needs_input
data: {"step": "model_config", "available_models": {...}, "recommended": {...}}

event: status
data: {"state": "downloading_user_models", "progress": {...}}

event: ready
data: {"state": "ready"}
```

The app connects to this on startup and renders whatever comes through.
If the app quits and reconnects, it calls `GET /api/bootstrap/status` for the
current snapshot, then reconnects to the SSE stream.

#### `POST /api/bootstrap/start`

Triggers the bootstrap sequence. If already running, returns current status.
If already complete, returns ready status.

#### `POST /api/bootstrap/retry`

Retries from the last failed step. Validates completed checkpoints first.

#### `POST /api/upgrade`

Server-initiated upgrade:
1. `pip install -e ".[voice]"` from the project root
2. Writes a `restart_requested` flag
3. Returns `{"restart_required": true}`
4. The app calls `launchctl bootout` + `launchctl bootstrap` to restart the daemon
5. Daemon restarts with new code, validates health

### 3.5 Server-Side Bootstrap Implementation

```python
# src/giva/bootstrap.py

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CHECKPOINT_PATH = Path("~/.local/share/giva/bootstrap.json").expanduser()
DEFAULT_MODEL = "mlx-community/Qwen3-8B-4bit"


class BootstrapStep(str, Enum):
    VENV_OK = "venv_ok"
    DEFAULT_MODEL = "default_model"
    MODEL_CONFIG = "model_config"
    USER_MODELS = "user_models"
    VALIDATION = "validation"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    WAITING = "waiting"  # needs user input


@dataclass
class BootstrapState:
    """Server-side bootstrap state, persisted to bootstrap.json."""
    checkpoint: str = "venv_ok"  # giva-setup already got us this far
    steps: dict[str, str] = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    error: Optional[str] = None
    updated_at: str = ""

    @property
    def is_ready(self) -> bool:
        return self.checkpoint == "ready"

    @property
    def needs_user_input(self) -> bool:
        return self.checkpoint == "configuring_models"

    @classmethod
    def load(cls) -> "BootstrapState":
        if CHECKPOINT_PATH.exists():
            try:
                data = json.loads(CHECKPOINT_PATH.read_text())
                return cls(**{k: v for k, v in data.items() if k != "version"})
            except Exception:
                pass
        return cls()

    def save(self):
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, **asdict(self)}
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        CHECKPOINT_PATH.write_text(json.dumps(data, indent=2))

    def complete_step(self, step: str):
        self.steps[step] = "done"
        self.error = None
        self.save()

    def fail_step(self, step: str, error: str):
        self.steps[step] = "failed"
        self.checkpoint = "failed"
        self.error = error
        self.save()


async def run_bootstrap(app) -> None:
    """Run remaining bootstrap steps. Called from lifespan or /api/bootstrap/start."""
    state: BootstrapState = app.state.bootstrap
    notify = app.state.bootstrap_notify  # asyncio.Event for SSE

    # Step 1: Default model download (if not already done)
    if state.steps.get("default_model") != "done":
        state.checkpoint = "downloading_default_model"
        state.steps["default_model"] = "running"
        state.save()
        notify.set()
        try:
            await _download_model_with_progress(DEFAULT_MODEL, state, "default_model", notify)
            state.complete_step("default_model")
        except Exception as e:
            state.fail_step("default_model", str(e))
            notify.set()
            return

    # Step 2: Model configuration
    # Check if user has already configured models
    from giva.models import is_model_setup_complete
    if not is_model_setup_complete():
        state.checkpoint = "configuring_models"
        state.steps["model_config"] = "waiting"
        state.save()
        notify.set()
        # STOP HERE — wait for user to POST /api/models/select
        # The select endpoint will call resume_after_model_config()
        return

    state.complete_step("model_config")

    # Step 3: Download user-selected models (if different from default)
    await _download_user_models(app, state, notify)

    # Step 4: Validation
    state.checkpoint = "validating"
    state.steps["validation"] = "running"
    state.save()
    notify.set()
    try:
        await _validate_models(app)
        state.complete_step("validation")
        state.checkpoint = "ready"
        state.save()
        notify.set()
    except Exception as e:
        state.fail_step("validation", str(e))
        notify.set()
```

### 3.6 What Stays in Swift

The SwiftUI app becomes a thin launcher + observer:

```swift
@MainActor
class BootstrapManager: ObservableObject {
    // State from server
    @Published var serverStatus: BootstrapStatusResponse?
    @Published var isServerReachable = false
    @Published var isSettingUp = false  // giva-setup running

    // Only paths the app needs to know
    static let dataDir = /* ~/.local/share/giva */
    static let venvPython = /* dataDir/.venv/bin/python3 */
    static let setupScript = /* Bundle or repo: scripts/giva-setup.py */
    static let launchdPlist = /* ~/Library/LaunchAgents/com.giva.server.plist */

    /// Full startup sequence
    func start() async {
        // Phase 1: Ensure venv exists (the ONE thing we can't delegate)
        if !isVenvHealthy() {
            isSettingUp = true
            await runSetupScript()
            isSettingUp = false
        }

        // Phase 2: Ensure daemon is running (launchctl — must be us)
        ensureLaunchdLoaded()

        // Phase 3: Wait for server to be reachable
        isServerReachable = await waitForHealth(timeout: 60)
        guard isServerReachable else {
            // show error: server didn't start
            return
        }

        // Phase 4: Observe the server's bootstrap state
        // Everything from here is just reading and displaying
        await observeBootstrapStatus()
    }

    /// The ONLY subprocess we ever run: giva-setup.py
    private func runSetupScript() async {
        let projectRoot = resolveProjectRoot()
        // giva-setup writes JSON lines to stdout — we parse and display
        let stream = ProcessStream(
            executable: findSystemPython(),
            arguments: [Self.setupScript, "--project-root", projectRoot]
        )
        for await line in stream {
            // Parse {"step": "...", "status": "...", "detail": "..."}
            // Update UI
        }
    }

    /// launchctl bootstrap — must be done by the app, not the daemon
    private func ensureLaunchdLoaded() {
        // bootout (ignore error) then bootstrap
    }

    /// Connect to SSE stream and mirror server state
    private func observeBootstrapStatus() async {
        // Initial snapshot
        serverStatus = try? await api.getBootstrapStatus()

        // Then stream updates
        for try await event in api.streamBootstrapStatus() {
            serverStatus = event
        }
    }
}
```

**What's gone from Swift:**
- `pip install` — moved to `giva-setup.py`
- `snapshot_download` — moved to server
- Dirty flag / UserDefaults state — replaced by `bootstrap.json`
- Git commit comparison — server handles via `/api/upgrade`
- Model download progress polling — server SSE
- All subprocess management except `giva-setup` and `launchctl`

### 3.7 Detailed Responsibility Migration

| Current (Swift) | New Owner | Mechanism |
|---|---|---|
| Find Python 3.11+ | `giva-setup.py` | Script finds python, creates venv |
| Resolve project root | App passes `--project-root` to `giva-setup` | App derives from bundle, passes as arg |
| Create venv | `giva-setup.py` | Script creates venv, verifies |
| pip install -e ".[voice]" | `giva-setup.py` | Script installs, verifies import |
| Download default model | `giva-server` (bootstrap) | SSE progress via `/api/bootstrap/stream` |
| Write launchd plist | `giva-setup.py` | Script writes plist template |
| `launchctl bootstrap` | **App (stays)** | Only thing that must be app-side |
| Wait for health | **App (stays)** | Polls `/api/health` |
| Dirty flag (UserDefaults) | Server: `bootstrap.json` | File-based checkpoint |
| Git commit check | Server: `/api/upgrade` | Server compares on startup |
| Model selection UI data | Server: `/api/models/available` | Already server-side |
| Model download + progress | Server: `/api/models/download` | Already server-side (SSE) |
| Upgrade (nuke + rebuild) | Server: `/api/upgrade` | Server re-pips, signals restart |
| Reset | Server: `/api/reset` | Already server-side |

### 3.8 Lifecycle Scenarios

#### Scenario A: Fresh Install (First Launch)

```
1. App starts. No venv exists.
2. App runs giva-setup.py --project-root ~/Developer/Giva
   - giva-setup finds python, creates venv, pip installs, writes plist
   - Streams JSON progress lines to app for UI
3. App calls launchctl bootstrap (loads the plist → daemon starts)
4. App polls /api/health → 200 OK
5. App connects to GET /api/bootstrap/stream
6. Server: bootstrap.json says checkpoint=venv_ok
   - Starts downloading default model → emits SSE progress
7. User can QUIT APP HERE. Download continues in daemon.
8. User reopens app → GET /api/bootstrap/status → "downloading 67%"
   → Reconnects to SSE stream → progress continues
9. Server finishes default model → checkpoint=configuring_models, needs_input=true
10. App shows ModelSetupView. User picks models.
11. User POST /api/models/select → server saves config
12. Server downloads user models → SSE progress events
13. Server validates models → checkpoint=ready
14. App receives "ready" event → shows MainPanelView
```

#### Scenario B: App Quit During Model Download

```
1. App is showing download progress (67%).
2. User quits app (Cmd+Q or close popover).
3. Daemon continues downloading. bootstrap.json updates.
4. User reopens app.
5. App: venv exists → skip giva-setup.
6. App: launchctl bootstrap → already loaded (error 37, ignored).
7. App: GET /api/health → 200 OK.
8. App: GET /api/bootstrap/status → downloading at 83%.
9. App: connects to SSE → picks up live progress.
10. Seamless continuation. User never lost progress.
```

#### Scenario C: Crash During Setup

```
1. Daemon crashes mid-download (OOM, disk full, etc).
2. launchd restarts daemon (KeepAlive.SuccessfulExit: false).
3. Daemon starts → reads bootstrap.json → checkpoint=downloading_user_models
4. Validates completed steps (default model exists? → yes).
5. Resumes download (snapshot_download is idempotent/resumable).
6. If app is open → SSE stream reconnects, shows progress.
7. If app is closed → download completes silently. Next open shows "ready".
```

#### Scenario D: Upgrade (Source Code Changed)

```
1. App starts. Venv exists. Server healthy.
2. App: GET /api/health → commit doesn't match local git HEAD.
3. App: POST /api/upgrade
4. Server: pip install -e ".[voice]" in background thread
5. Server: returns {restart_required: true}
6. App: launchctl bootout + bootstrap (restarts daemon with new code)
7. App: polls /api/health → new commit matches
8. App: GET /api/bootstrap/status → ready
```

#### Scenario E: Reset

```
1. User triggers reset from settings.
2. App: POST /api/reset
3. Server: wipes DB, caches, user config.toml
4. Server: bootstrap.json → checkpoint=configuring_models
5. App: receives status update → shows ModelSetupView
6. No venv rebuild. No model re-download. Fast.
```

### 3.9 `giva-setup.py` Script Specification

```python
#!/usr/bin/env python3
"""One-shot bootstrap: create venv, install giva, write launchd plist.

This script runs with SYSTEM python (not the venv).
It is the only code path that creates the venv from scratch.
Once done, the giva-server daemon takes over all remaining setup.

Usage:
    python3 giva-setup.py --project-root /path/to/Giva

Output: JSON lines to stdout for the app to parse.
Exit code: 0 on success, 1 on failure.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "giva"
VENV_DIR = DATA_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python3"
VENV_PIP = VENV_DIR / "bin" / "pip"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.giva.server.plist"


def emit(step: str, status: str, **kwargs):
    """Emit a JSON progress line to stdout."""
    msg = {"step": step, "status": status, **kwargs}
    print(json.dumps(msg), flush=True)


def find_python() -> str:
    """Find Python 3.11+ on the system."""
    emit("finding_python", "running")
    candidates = [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            version = _get_version(path)
            if version and version >= (3, 11):
                emit("finding_python", "done", detail=path)
                return path

    emit("finding_python", "failed", error="Python 3.11+ not found")
    sys.exit(1)


def create_venv(python: str):
    """Create the venv if it doesn't exist or is broken."""
    emit("creating_venv", "running")

    if VENV_PYTHON.exists() and _venv_healthy():
        emit("creating_venv", "done", detail="already exists")
        return

    # Remove broken venv
    if VENV_DIR.exists():
        import shutil
        shutil.rmtree(VENV_DIR)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([python, "-m", "venv", str(VENV_DIR)], check=True)
    emit("creating_venv", "done")


def install_deps(project_root: str):
    """Install giva into the venv."""
    emit("installing_deps", "running")

    # Upgrade pip
    subprocess.run(
        [str(VENV_PIP), "install", "--upgrade", "pip"],
        capture_output=True, check=True,
    )

    # Install project
    subprocess.run(
        [str(VENV_PIP), "install", "-e", ".[voice]"],
        cwd=project_root, capture_output=True, check=True,
    )

    # Verify
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", "import giva; print(giva.__version__)"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        emit("installing_deps", "failed", error="giva not importable after install")
        sys.exit(1)

    emit("installing_deps", "done", detail=f"v{result.stdout.strip()}")


def write_plist():
    """Write the launchd plist for the giva-server daemon."""
    emit("writing_plist", "running")
    # ... write plist XML ...
    emit("writing_plist", "done")


def write_checkpoint():
    """Write initial bootstrap checkpoint so the server knows where to resume."""
    checkpoint = {
        "version": 1,
        "checkpoint": "venv_ok",
        "steps": {"venv_ok": "done"},
        "progress": {},
        "error": None,
    }
    checkpoint_path = DATA_DIR / "bootstrap.json"
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2))
    emit("checkpoint", "done")


def _venv_healthy() -> bool:
    """Check if the venv python works."""
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "--version"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
```

### 3.10 Fixing the Progress Bar Bugs

With the server owning all downloads, both the initial default model download and
user-selected model downloads use the **same code path**: the SSE
`/api/bootstrap/stream` or `/api/models/download` endpoint.

**Fix the size mismatch** (`_get_cache_size`):

```python
_WEIGHT_EXTS = frozenset((".safetensors", ".bin", ".gguf"))

def _get_cache_size(model_id: str) -> int:
    """Get bytes of weight files on disk, including incomplete downloads."""
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / ("models--" + model_id.replace("/", "--"))
    if not model_dir.is_dir():
        return 0
    total = 0
    for f in model_dir.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        # Count weight files and their in-progress temp versions
        if any(name.endswith(ext) or name.endswith(ext + ".incomplete")
               for ext in _WEIGHT_EXTS):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total
```

**Handle zero total_bytes:**

```python
if total_bytes == 0:
    # Fallback: estimate from model name heuristics
    total_bytes = _estimate_model_size(model_id)

# If still zero, emit indeterminate progress
if total_bytes == 0:
    pct = -1  # UI shows indeterminate progress bar
else:
    pct = min(round(cached / total_bytes * 100, 1), 99.9)
```

Swift UI handles `percent: -1`:
```swift
if percent < 0 {
    ProgressView()  // indeterminate spinner
    Text("\(downloadedMB) MB downloaded")
} else {
    ProgressView(value: percent, total: 100)
    Text("\(Int(percent))%")
}
```

---

## 4. Implementation Plan

### Phase 1: Fix Critical Bugs (No Architecture Change)

Quick wins that can ship immediately on the current architecture:

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1.1 | Fix `_get_cache_size` to count only weight files | `server.py` | S |
| 1.2 | Handle `total_bytes == 0` with indeterminate progress | `server.py` | S |
| 1.3 | Fix Retry to call `start()` instead of `runBootstrap()` | `BootstrapView.swift` | S |
| 1.4 | Remove double `connectToServer()` in `triggerUpgrade()` | `GivaViewModel.swift` | S |
| 1.5 | Fix `is_model_setup_complete()` to use TOML parsing | `models.py` | S |
| 1.6 | Add cancellation guard to `selectAndDownloadModels` | `GivaViewModel.swift` | S |

### Phase 2: Server-Side Bootstrap Migration

The core architectural change:

| # | Task | Files | Effort |
|---|------|-------|--------|
| 2.1 | Create `scripts/giva-setup.py` | New file | M |
| 2.2 | Create `src/giva/bootstrap.py` (state machine + checkpoint) | New file | L |
| 2.3 | Add bootstrap endpoints to `server.py` | `server.py` | M |
| 2.4 | Add bootstrap SSE stream to `server.py` | `server.py` | M |
| 2.5 | Integrate bootstrap into server `lifespan` | `server.py` | S |
| 2.6 | Unify model download: bootstrap + model-setup use same path | `server.py`, `bootstrap.py` | M |
| 2.7 | Add `/api/upgrade` endpoint | `server.py` | M |

### Phase 3: SwiftUI App Slim-Down

Rewrite the app to be a pure observer:

| # | Task | Files | Effort |
|---|------|-------|--------|
| 3.1 | Rewrite `BootstrapManager` as thin launcher + observer | `BootstrapManager.swift` | L |
| 3.2 | Remove all `runProcess` calls except `giva-setup` + `launchctl` | `BootstrapManager.swift` | M |
| 3.3 | Update `BootstrapView` to render from `BootstrapStatusResponse` | `BootstrapView.swift` | M |
| 3.4 | Update `ModelSetupView` to use bootstrap SSE (not separate download) | `ModelSetupView.swift` | M |
| 3.5 | Remove dirty flag, upgrade logic, git commit check from Swift | `BootstrapManager.swift` | S |
| 3.6 | Add `APIService` methods for new bootstrap endpoints | `APIService.swift` | S |
| 3.7 | Remove `BootstrapPhase` enum (server provides display strings) | `BootstrapManager.swift` | S |

### Phase 4: Polish

| # | Task | Effort |
|---|------|--------|
| 4.1 | Add `giva-setup` to app bundle (Build Phase copy) | S |
| 4.2 | Handle `giva-setup` not found (dev vs distributed app) | S |
| 4.3 | Smarter upgrade: pip-only first, full rebuild as fallback | M |
| 4.4 | Graceful daemon restart protocol (drain connections → exit → launchd restarts) | M |

---

## 5. API Contract Summary

### New Endpoints

```
GET  /api/bootstrap/status
  → BootstrapStatusResponse

GET  /api/bootstrap/stream     (SSE)
  → events: status, step_complete, needs_input, ready, error

POST /api/bootstrap/start
  → BootstrapStatusResponse

POST /api/bootstrap/retry
  → BootstrapStatusResponse

POST /api/upgrade
  Request:  {"project_root": "/path/to/Giva"}
  Response: {"success": true, "restart_required": true}
```

### Modified Endpoints

```
POST /api/models/select
  NEW BEHAVIOR: After saving config, resumes bootstrap
  (triggers user model download if bootstrap was waiting)

POST /api/models/download
  UNCHANGED but now also called internally by bootstrap.
  External callers still get SSE progress.

GET  /api/health
  UNCHANGED but bootstrap/status is the preferred readiness check.
```

### Response Types

```python
class BootstrapStepInfo(BaseModel):
    name: str
    status: str  # pending | running | done | failed | waiting
    progress: Optional[dict] = None  # for download steps
    error: Optional[str] = None

class BootstrapStatusResponse(BaseModel):
    state: str          # venv_ok | downloading_default_model | configuring_models |
                        # downloading_user_models | validating | ready | failed | upgrading
    ready: bool
    needs_user_input: bool
    steps: list[BootstrapStepInfo]
    error: Optional[str] = None
    display_message: str  # human-readable status string for the UI
```

---

## 6. Key Design Invariants

1. **The daemon never stops itself.** It signals `restart_required` and the app does
   `launchctl bootout + bootstrap`. If the app is not running, the daemon keeps running
   with old code until the next app launch.

2. **The checkpoint file is the single source of truth.** No UserDefaults, no in-memory
   state that can't be reconstructed. Daemon crash → restart → read checkpoint → resume.

3. **Every step is idempotent.** Running a step that's already done is a no-op.
   `snapshot_download` skips cached files. `pip install -e .` on an already-installed
   package is fast. Writing a plist that already exists just overwrites.

4. **The UI never blocks on the server.** If the server is unreachable, the UI shows
   "Connecting..." and polls. If the server is mid-bootstrap, the UI shows progress.
   The user can close and reopen at any time.

5. **Model download progress uses a single unified implementation.** Both bootstrap
   default-model download and user-model download go through the same progress-tracking
   code path — no more two implementations with different bugs.

6. **giva-setup is fire-and-forget.** It creates the venv and exits. It doesn't manage
   any ongoing state. If it fails, the app shows the error and offers retry. The damage
   is limited to a partial venv, which giva-setup cleans up on retry.
