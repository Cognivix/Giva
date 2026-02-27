"""Server-side bootstrap state machine.

Single authoritative state: ``checkpoint``.

The checkpoint follows a strict Markov chain — each value implies all
previous steps are done.  No separate ``steps_completed`` list; the
checkpoint IS the history.

Markov chain
~~~~~~~~~~~~
::

    unknown
      → downloading_default_model
      → awaiting_model_selection
      → downloading_user_models
      → validating
      → ready            ← models done, UI shows main panel
      → syncing          ← initial email/calendar sync
      → onboarding       ← LLM interview to learn user prefs
      → operational      ← fully running, scheduler active

Transient / overlay states (can happen from any post-ready checkpoint):

- ``failed``    — error; ``error`` field has details
- ``upgrading`` — pip install in progress (not currently set by server)

Persistence
~~~~~~~~~~~
Written to ``~/.local/share/giva/bootstrap.json`` so the daemon can
resume after crashes, restarts, or app quit/reopen cycles.

Observation
~~~~~~~~~~~
- ``/api/bootstrap/status`` — snapshot
- ``/api/bootstrap/stream`` — SSE live updates
- ``/api/session/stream``   — post-ready lifecycle (sync → onboard → operational)

The app never writes to the checkpoint file — only the server does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/Qwen3-8B-4bit"

_WEIGHT_EXTS = frozenset((".safetensors", ".bin", ".gguf"))

# Ordered list of checkpoints in the Markov chain.
# Each checkpoint implies all preceding ones are complete.
CHECKPOINT_ORDER = [
    "unknown",
    "downloading_default_model",
    "awaiting_model_selection",
    "downloading_user_models",
    "validating",
    "ready",
    "syncing",
    "onboarding",
    "operational",
]

# Set of checkpoints that can be considered "past ready" — the UI can
# show the main panel.
_PAST_READY = frozenset(CHECKPOINT_ORDER[CHECKPOINT_ORDER.index("ready"):])


def _checkpoint_path() -> Path:
    return Path("~/.local/share/giva/bootstrap.json").expanduser()


def _checkpoint_index(cp: str) -> int:
    """Return the ordinal position of *cp* in the Markov chain, or -1."""
    try:
        return CHECKPOINT_ORDER.index(cp)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class BootstrapState:
    """Mutable bootstrap state — single authoritative ``checkpoint``.

    The ``checkpoint`` field IS the state machine.  There is no separate
    list of completed steps; a later checkpoint implies all earlier ones
    are done.
    """

    checkpoint: str = "unknown"
    progress: dict = field(default_factory=dict)
    error: Optional[str] = None
    updated_at: Optional[str] = None

    # --- Derived properties (all computed from checkpoint) ---

    @property
    def is_ready(self) -> bool:
        """True when models are downloaded and validated (main UI can show)."""
        return self.checkpoint in _PAST_READY

    @property
    def is_operational(self) -> bool:
        """True when the full lifecycle is complete."""
        return self.checkpoint == "operational"

    @property
    def needs_user_input(self) -> bool:
        return self.checkpoint == "awaiting_model_selection"

    def past(self, checkpoint: str) -> bool:
        """True if current checkpoint is *at or past* ``checkpoint``."""
        return _checkpoint_index(self.checkpoint) >= _checkpoint_index(checkpoint)

    @property
    def display_message(self) -> str:
        messages = {
            "unknown": "Initializing...",
            "downloading_default_model": "Downloading base AI model...",
            "awaiting_model_selection": "Choose your AI models",
            "downloading_user_models": "Downloading selected models...",
            "validating": "Validating models...",
            "ready": "Ready!",
            "syncing": "Syncing your emails and calendar...",
            "onboarding": "Getting to know you...",
            "operational": "Ready!",
            "failed": f"Setup failed: {self.error or 'unknown error'}",
            "upgrading": "Upgrading...",
        }
        return messages.get(self.checkpoint, self.checkpoint)

    # --- Persistence ---

    @classmethod
    def load(cls) -> BootstrapState:
        """Load state from the checkpoint file, or return default."""
        path = _checkpoint_path()
        if path.exists():
            try:
                raw = json.loads(path.read_text())

                # Migrate from v1 (steps_completed-based) to v2 (checkpoint-only).
                # If the file has steps_completed, compute the highest checkpoint
                # that those completed steps imply.
                if "steps_completed" in raw:
                    return cls._migrate_v1(raw)

                return cls(
                    checkpoint=raw.get("checkpoint", "unknown"),
                    progress=raw.get("progress", {}),
                    error=raw.get("error"),
                    updated_at=raw.get("updated_at"),
                )
            except Exception as e:
                log.warning("Failed to load bootstrap checkpoint: %s", e)
        return cls()

    @classmethod
    def _migrate_v1(cls, raw: dict) -> BootstrapState:
        """Migrate a v1 bootstrap.json (with steps_completed) to v2.

        The v1 format tracked ``steps_completed`` alongside ``checkpoint``.
        In v2, only ``checkpoint`` matters.  We honour the v1 checkpoint
        directly unless it's inconsistent, in which case we derive from
        the completed steps.
        """
        cp = raw.get("checkpoint", "unknown")

        # If checkpoint is already a valid, well-known state, trust it.
        if cp in CHECKPOINT_ORDER or cp in ("failed", "upgrading"):
            log.info("Migrating bootstrap v1→v2: keeping checkpoint=%s", cp)
        else:
            # Derive from steps_completed
            steps = set(raw.get("steps_completed", []))
            if "onboarding" in steps:
                cp = "operational"
            elif "initial_sync" in steps:
                cp = "onboarding"
            elif "validation" in steps:
                cp = "ready"
            elif "user_models" in steps:
                cp = "validating"
            elif "model_config" in steps:
                cp = "downloading_user_models"
            elif "default_model" in steps:
                cp = "awaiting_model_selection"
            else:
                cp = "unknown"
            log.info("Migrating bootstrap v1→v2: derived checkpoint=%s", cp)

        state = cls(
            checkpoint=cp,
            progress=raw.get("progress", {}),
            error=raw.get("error"),
            updated_at=raw.get("updated_at"),
        )
        state.save()  # Persist the v2 format
        return state

    def save(self) -> None:
        """Persist current state to disk."""
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = _checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 2,
            "checkpoint": self.checkpoint,
            "progress": self.progress,
            "error": self.error,
            "updated_at": self.updated_at,
        }
        path.write_text(json.dumps(data, indent=2))

    # --- State transitions (all move checkpoint forward) ---

    def advance(self, checkpoint: str) -> None:
        """Move to the next checkpoint.  Clears error and progress."""
        self.checkpoint = checkpoint
        self.error = None
        self.progress = {}
        self.save()

    def fail(self, error: str) -> None:
        """Move to 'failed'.  Preserves which checkpoint we were at in error."""
        self.checkpoint = "failed"
        self.error = error
        self.save()

    def mark_ready(self) -> None:
        self.advance("ready")

    def mark_syncing(self) -> None:
        self.advance("syncing")

    def mark_onboarding(self) -> None:
        self.advance("onboarding")

    def mark_operational(self) -> None:
        self.advance("operational")

    # --- Snapshot for API ---

    def to_response(self) -> dict:
        """Build the API response dict."""
        return {
            "state": self.checkpoint,
            "ready": self.is_ready,
            "needs_user_input": self.needs_user_input,
            "progress": self.progress,
            "error": self.error,
            "display_message": self.display_message,
        }


# ---------------------------------------------------------------------------
# Notifier (for SSE streaming)
# ---------------------------------------------------------------------------


class BootstrapNotifier:
    """Thread-safe notification hub for SSE consumers."""

    def __init__(self):
        self._event = asyncio.Event()

    def notify(self) -> None:
        """Signal all SSE consumers that state changed."""
        self._event.set()

    async def wait(self, timeout: float = 2.0) -> bool:
        """Wait for a notification or timeout.  Returns True if notified."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            self._event.clear()
            return True
        except asyncio.TimeoutError:
            return False


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def get_cache_size(model_id: str) -> int:
    """Get bytes of weight files on disk for a model (including .incomplete).

    HuggingFace blobs use content hashes as filenames (no original extension),
    so we match:
    - Committed blobs ≥10 MB = weight data (hash-named, no extension)
    - ``.incomplete`` temp files = in-progress downloads

    For ``.incomplete`` files we use ``st_blocks * 512`` (actual disk usage)
    instead of ``st_size`` because HF's xet downloader pre-allocates sparse
    files at the full target size while writing data into them randomly.
    Using ``st_size`` would over-report progress.
    """
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / ("models--" + model_id.replace("/", "--"))
    if not model_dir.is_dir():
        return 0

    blobs_dir = model_dir / "blobs"
    if not blobs_dir.is_dir():
        return 0

    total = 0
    seen_inodes: set[int] = set()  # avoid double-counting via symlinks

    for f in blobs_dir.iterdir():
        if not f.is_file():
            continue
        try:
            st = f.stat()
            if st.st_ino in seen_inodes:
                continue
            seen_inodes.add(st.st_ino)

            if f.name.endswith(".incomplete"):
                # Sparse file — use actual blocks on disk
                total += st.st_blocks * 512
            elif st.st_size >= 10 * 1024 * 1024:
                # Committed weight blob
                total += st.st_size
        except OSError:
            pass

    return total


def get_model_total_bytes(model_id: str) -> int:
    """Get total weight file size from HuggingFace API."""
    try:
        from giva.models import _get_repo_size_bytes
        return _get_repo_size_bytes(model_id)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Bootstrap runner
# ---------------------------------------------------------------------------


async def run_bootstrap(app) -> None:
    """Run remaining bootstrap steps up to 'ready'.

    Called from server lifespan on startup (if not ready) or from
    ``POST /api/bootstrap/start``.  Each step is idempotent — re-running
    after a crash picks up where it left off because ``checkpoint`` tells
    us exactly where we are.

    Post-ready lifecycle (sync → onboarding → operational) is driven by
    ``/api/session/stream`` when the UI connects.
    """
    state: BootstrapState = app.state.bootstrap
    notifier: BootstrapNotifier = app.state.bootstrap_notifier
    loop = asyncio.get_event_loop()

    # Step 1: Download default model (filter/bootstrap advisor)
    if not state.past("awaiting_model_selection"):
        state.advance("downloading_default_model")
        notifier.notify()

        try:
            await _download_model_with_progress(
                DEFAULT_MODEL, state, notifier, loop
            )
            # Don't advance yet — next step decides checkpoint
        except Exception as e:
            log.error("Default model download failed: %s", e)
            state.fail(str(e))
            notifier.notify()
            return

    # Step 2: Check if user has configured models
    if not state.past("downloading_user_models"):
        from giva.models import is_model_setup_complete

        setup_done = is_model_setup_complete()
        log.info(
            "Bootstrap step 2: is_model_setup_complete=%s (checkpoint=%s)",
            setup_done, state.checkpoint,
        )
        if setup_done:
            # User already configured — skip selection
            pass
        else:
            # Park here — wait for user to POST /api/models/select
            state.advance("awaiting_model_selection")
            notifier.notify()
            log.info("Bootstrap: parked at awaiting_model_selection")
            return  # Stop. resume_after_model_selection() continues.

    # Step 3: Download user-selected models
    if not state.past("validating"):
        await _download_user_models(app, state, notifier, loop)
        if state.checkpoint == "failed":
            return

    # Step 4: Validation
    if not state.past("ready"):
        state.advance("validating")
        notifier.notify()
        try:
            await _validate_models(app, loop)
        except Exception as e:
            log.error("Model validation failed: %s", e)
            state.fail(str(e))
            notifier.notify()
            return

    # Models are ready — mark ready.
    # Post-ready lifecycle is driven by /api/session/stream.
    state.mark_ready()
    notifier.notify()
    log.info("Bootstrap: models ready. Post-ready lifecycle starts when UI connects.")


async def resume_after_model_selection(app) -> None:
    """Called after POST /api/models/select to continue bootstrap."""
    # Continue from user_models download step
    await run_bootstrap(app)


def _cleanup_stale_incomplete(model_id: str) -> None:
    """Remove .incomplete files left from a previous interrupted download.

    HF's xet downloader can leave sparse ``.incomplete`` files that block
    a fresh ``snapshot_download()`` from making progress.  Removing them
    lets the next call re-create them cleanly.
    """
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    blobs_dir = cache_root / ("models--" + model_id.replace("/", "--")) / "blobs"
    if not blobs_dir.is_dir():
        return
    for f in blobs_dir.iterdir():
        if f.is_file() and f.name.endswith(".incomplete"):
            try:
                f.unlink()
                log.info("Cleaned stale incomplete: %s", f.name[:16])
            except OSError:
                pass


async def _download_model_with_progress(
    model_id: str,
    state: BootstrapState,
    notifier: BootstrapNotifier,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Download a model with progress tracking via cache directory polling."""
    from giva.models import download_model, is_model_downloaded

    # Skip if already downloaded
    if is_model_downloaded(model_id):
        state.progress[model_id] = {"percent": 100, "downloaded_mb": 0, "total_mb": 0}
        state.save()
        notifier.notify()
        return

    # Clean up stale .incomplete files from previous interrupted downloads.
    await loop.run_in_executor(None, _cleanup_stale_incomplete, model_id)

    # Get total size
    total_bytes = await loop.run_in_executor(None, get_model_total_bytes, model_id)
    total_mb = round(total_bytes / (1024 ** 2), 1) if total_bytes else 0

    # Start download in thread
    done_event = asyncio.Event()
    download_error: list[str] = []

    def _run():
        try:
            download_model(model_id)
        except Exception as e:
            download_error.append(str(e))
        finally:
            loop.call_soon_threadsafe(done_event.set)

    loop.run_in_executor(None, _run)

    # Poll progress
    while not done_event.is_set():
        cached = await loop.run_in_executor(
            None, get_cache_size, model_id
        )
        dl_mb = round(cached / (1024 ** 2), 1)
        if total_bytes > 0:
            pct = min(round(cached / total_bytes * 100, 1), 99.9)
        else:
            pct = -1  # indeterminate

        state.progress[model_id] = {
            "percent": pct, "downloaded_mb": dl_mb, "total_mb": total_mb,
        }
        state.save()
        notifier.notify()

        try:
            await asyncio.wait_for(done_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    if download_error:
        raise RuntimeError(download_error[0])

    # Final 100%
    state.progress[model_id] = {
        "percent": 100, "downloaded_mb": total_mb, "total_mb": total_mb,
    }
    state.save()
    notifier.notify()


async def _download_user_models(
    app, state: BootstrapState, notifier: BootstrapNotifier,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Download user-configured models (assistant + filter + VLM).

    Skips the Apple on-device model (``"apple"``) since it requires no
    download — it's part of macOS.  Skips the VLM model if VLM is
    disabled or no model is configured.
    """
    from giva.llm.apple_adapter import is_apple_model

    config = app.state.config
    models_to_download = set()
    models_to_download.add(config.llm.model)
    if not is_apple_model(config.llm.filter_model):
        models_to_download.add(config.llm.filter_model)

    # Include VLM model if configured and enabled
    if config.vlm.enabled and config.vlm.model:
        models_to_download.add(config.vlm.model)

    state.advance("downloading_user_models")
    notifier.notify()

    for model_id in models_to_download:
        try:
            await _download_model_with_progress(
                model_id, state, notifier, loop
            )
        except Exception as e:
            log.error("User model download failed (%s): %s", model_id, e)
            state.fail(f"{model_id}: {e}")
            notifier.notify()
            return

    # Don't advance — caller decides the next checkpoint


async def _validate_models(app, loop: asyncio.AbstractEventLoop) -> None:
    """Verify that configured models can be loaded.

    The Apple on-device model (``"apple"``) is validated via the SDK's
    availability check instead of the HuggingFace cache.
    VLM model is validated only if VLM is enabled and configured.
    """
    from giva.llm.apple_adapter import check_apple_model_availability, is_apple_model
    from giva.models import is_model_downloaded

    config = app.state.config

    def _check():
        if not is_model_downloaded(config.llm.model):
            raise RuntimeError(f"Assistant model not downloaded: {config.llm.model}")

        if is_apple_model(config.llm.filter_model):
            available, reason = check_apple_model_availability()
            if not available:
                log.warning("Apple Foundation Model not available: %s", reason)
                # Don't fail bootstrap — the model may become ready later
                # (e.g. still downloading in the background by macOS).
        elif not is_model_downloaded(config.llm.filter_model):
            raise RuntimeError(f"Filter model not downloaded: {config.llm.filter_model}")

        # Validate VLM model if enabled
        if config.vlm.enabled and config.vlm.model:
            if not is_model_downloaded(config.vlm.model):
                raise RuntimeError(f"VLM model not downloaded: {config.vlm.model}")

    await loop.run_in_executor(None, _check)


# ---------------------------------------------------------------------------
# Post-ready lifecycle helpers
# ---------------------------------------------------------------------------


def complete_onboarding(app) -> None:
    """Called by the session endpoint when onboarding finishes."""
    state: BootstrapState = app.state.bootstrap
    notifier: BootstrapNotifier = app.state.bootstrap_notifier
    state.mark_operational()
    notifier.notify()
    log.info("Bootstrap: onboarding complete — operational")

    # Start the background scheduler now that we're fully operational
    from giva.server import _start_scheduler
    _start_scheduler(app)
