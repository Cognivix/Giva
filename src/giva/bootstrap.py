"""Server-side bootstrap state machine.

Manages the post-venv setup: model downloads, config validation, and readiness.
State is persisted to ``~/.local/share/giva/bootstrap.json`` so the daemon can
resume after crashes, restarts, or app quit/reopen cycles.

The SwiftUI app observes this state via ``/api/bootstrap/status`` (snapshot)
and ``/api/bootstrap/stream`` (SSE live updates).  The app never writes to the
checkpoint file — only the server does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "mlx-community/Qwen3-8B-4bit"

_WEIGHT_EXTS = frozenset((".safetensors", ".bin", ".gguf"))


def _checkpoint_path() -> Path:
    return Path("~/.local/share/giva/bootstrap.json").expanduser()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class StepInfo:
    """Status of a single bootstrap step."""
    name: str
    status: str = "pending"  # pending | running | done | failed | waiting
    progress: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "status": self.status}
        if self.progress is not None:
            d["progress"] = self.progress
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class BootstrapState:
    """Mutable bootstrap state, persisted to bootstrap.json."""

    checkpoint: str = "unknown"
    steps_completed: list[str] = field(default_factory=list)
    current_step: Optional[str] = None
    progress: dict = field(default_factory=dict)
    error: Optional[str] = None
    updated_at: Optional[str] = None

    # --- Derived properties ---

    @property
    def is_ready(self) -> bool:
        return self.checkpoint == "ready"

    @property
    def needs_user_input(self) -> bool:
        return self.checkpoint == "awaiting_model_selection"

    @property
    def display_message(self) -> str:
        messages = {
            "unknown": "Initializing...",
            "venv_ok": "Environment ready",
            "downloading_default_model": "Downloading base AI model...",
            "awaiting_model_selection": "Choose your AI models",
            "downloading_user_models": "Downloading selected models...",
            "validating": "Validating models...",
            "ready": "Ready!",
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
                return cls(
                    checkpoint=raw.get("checkpoint", "unknown"),
                    steps_completed=raw.get("steps_completed", []),
                    current_step=raw.get("current_step"),
                    progress=raw.get("progress", {}),
                    error=raw.get("error"),
                    updated_at=raw.get("updated_at"),
                )
            except Exception as e:
                log.warning("Failed to load bootstrap checkpoint: %s", e)
        return cls()

    def save(self) -> None:
        """Persist current state to disk."""
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = _checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "checkpoint": self.checkpoint,
            "steps_completed": self.steps_completed,
            "current_step": self.current_step,
            "progress": self.progress,
            "error": self.error,
            "updated_at": self.updated_at,
        }
        path.write_text(json.dumps(data, indent=2))

    # --- State transitions ---

    def complete_step(self, step: str) -> None:
        if step not in self.steps_completed:
            self.steps_completed.append(step)
        self.current_step = None
        self.error = None
        self.save()

    def start_step(self, step: str, checkpoint: str) -> None:
        self.checkpoint = checkpoint
        self.current_step = step
        self.error = None
        self.save()

    def fail(self, step: str, error: str) -> None:
        self.checkpoint = "failed"
        self.current_step = step
        self.error = error
        self.save()

    def mark_ready(self) -> None:
        self.checkpoint = "ready"
        self.current_step = None
        self.error = None
        self.progress = {}
        self.save()

    # --- Snapshot for API ---

    def to_response(self) -> dict:
        """Build the API response dict."""
        all_steps = [
            "default_model", "model_config", "user_models", "validation"
        ]
        steps = []
        for name in all_steps:
            if name in self.steps_completed:
                steps.append(StepInfo(name=name, status="done").to_dict())
            elif name == self.current_step:
                status = "waiting" if self.needs_user_input else "running"
                si = StepInfo(name=name, status=status)
                if self.progress:
                    si.progress = self.progress
                if self.error:
                    si.error = self.error
                steps.append(si.to_dict())
            else:
                steps.append(StepInfo(name=name, status="pending").to_dict())

        return {
            "state": self.checkpoint,
            "ready": self.is_ready,
            "needs_user_input": self.needs_user_input,
            "steps": steps,
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
        self._lock = asyncio.Lock()

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
    """Get bytes of weight files on disk for a model (including .incomplete)."""
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / ("models--" + model_id.replace("/", "--"))
    if not model_dir.is_dir():
        return 0
    total = 0
    for f in model_dir.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        if any(name.endswith(ext) or name.endswith(ext + ".incomplete")
               for ext in _WEIGHT_EXTS):
            try:
                total += f.stat().st_size
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
    """Run remaining bootstrap steps.

    Called from server lifespan on startup (if not ready) or from
    ``POST /api/bootstrap/start``.  Each step is idempotent — re-running
    after a crash picks up where it left off.
    """
    state: BootstrapState = app.state.bootstrap
    notifier: BootstrapNotifier = app.state.bootstrap_notifier
    loop = asyncio.get_event_loop()

    # Step 1: Download default model (filter/bootstrap advisor)
    if "default_model" not in state.steps_completed:
        state.start_step("default_model", "downloading_default_model")
        notifier.notify()

        try:
            await _download_model_with_progress(
                DEFAULT_MODEL, state, "default_model", notifier, loop
            )
            state.complete_step("default_model")
            notifier.notify()
        except Exception as e:
            log.error("Default model download failed: %s", e)
            state.fail("default_model", str(e))
            notifier.notify()
            return

    # Step 2: Check if user has configured models
    if "model_config" not in state.steps_completed:
        from giva.models import is_model_setup_complete

        if is_model_setup_complete():
            state.complete_step("model_config")
            notifier.notify()
        else:
            # Park here — wait for user to POST /api/models/select
            state.start_step("model_config", "awaiting_model_selection")
            notifier.notify()
            return  # Stop. resume_after_model_selection() continues.

    # Step 3: Download user-selected models
    if "user_models" not in state.steps_completed:
        await _download_user_models(app, state, notifier, loop)
        if state.checkpoint == "failed":
            return

    # Step 4: Validation
    if "validation" not in state.steps_completed:
        state.start_step("validation", "validating")
        notifier.notify()
        try:
            await _validate_models(app, loop)
            state.complete_step("validation")
        except Exception as e:
            log.error("Model validation failed: %s", e)
            state.fail("validation", str(e))
            notifier.notify()
            return

    # All done!
    state.mark_ready()
    notifier.notify()
    log.info("Bootstrap complete — server is ready.")


async def resume_after_model_selection(app) -> None:
    """Called after POST /api/models/select to continue bootstrap."""
    state: BootstrapState = app.state.bootstrap
    notifier: BootstrapNotifier = app.state.bootstrap_notifier

    state.complete_step("model_config")
    notifier.notify()

    # Continue with remaining steps
    await run_bootstrap(app)


async def _download_model_with_progress(
    model_id: str,
    state: BootstrapState,
    step_name: str,
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
    """Download user-configured models (assistant + filter)."""
    config = app.state.config
    models_to_download = set()
    models_to_download.add(config.llm.model)
    models_to_download.add(config.llm.filter_model)

    state.start_step("user_models", "downloading_user_models")
    state.progress = {}
    notifier.notify()

    for model_id in models_to_download:
        try:
            await _download_model_with_progress(
                model_id, state, "user_models", notifier, loop
            )
        except Exception as e:
            log.error("User model download failed (%s): %s", model_id, e)
            state.fail("user_models", f"{model_id}: {e}")
            notifier.notify()
            return

    state.complete_step("user_models")
    notifier.notify()


async def _validate_models(app, loop: asyncio.AbstractEventLoop) -> None:
    """Verify that configured models can be loaded."""
    from giva.models import is_model_downloaded

    config = app.state.config

    def _check():
        if not is_model_downloaded(config.llm.model):
            raise RuntimeError(f"Assistant model not downloaded: {config.llm.model}")
        if not is_model_downloaded(config.llm.filter_model):
            raise RuntimeError(f"Filter model not downloaded: {config.llm.filter_model}")

    await loop.run_in_executor(None, _check)
