"""FastAPI server exposing Giva's intelligence layer over REST + SSE.

Provides a clean API for the SwiftUI menu bar app (or any HTTP client).
Streaming endpoints use Server-Sent Events (SSE) for real-time token delivery.
Voice endpoints provide TTS audio chunks in SSE and STT transcription.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from giva import __version__
from giva.config import load_config
from giva.db.store import Store

log = logging.getLogger(__name__)


def _get_git_commit() -> str:
    """Get the current git commit hash of the giva source tree.

    Uses the project root (parent of the giva package) to find the git repo.
    Returns the short commit hash, or "unknown" if not in a git repo.
    """
    import subprocess
    from pathlib import Path

    # Walk up from the giva package to find .git
    pkg_dir = Path(__file__).resolve().parent  # src/giva/
    repo_root = pkg_dir.parent.parent  # project root (contains .git)
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_root),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# Resolved once at import time so it's fast on every health check
_GIT_COMMIT = _get_git_commit()

# Lock to serialize all LLM calls (MLX ModelManager is not thread-safe)
_llm_lock = threading.Lock()

# Separate lock for voice models (TTS/STT use different MLX models)
_voice_lock = threading.Lock()

# Cached STT engine singleton (lazy-initialised, protected by _voice_lock)
_stt_engine = None


def _get_stt_engine(voice_config):
    """Return a shared STTEngine, creating it on first call."""
    global _stt_engine
    if _stt_engine is None:
        from giva.audio.stt import STTEngine
        _stt_engine = STTEngine(voice_config)
    return _stt_engine

# --- Whisper Hallucination Filter ---
# Common phrases Whisper generates when fed silence or near-silence audio.
_WHISPER_HALLUCINATION_PATTERNS: set[str] = {
    "you",
    "thank you",
    "thank you.",
    "thanks",
    "thanks.",
    "thanks for watching",
    "thanks for watching.",
    "thanks for watching!",
    "thank you for watching",
    "thank you for watching.",
    "thank you for watching!",
    "bye",
    "bye.",
    "bye bye",
    "bye bye.",
    "goodbye",
    "goodbye.",
    "hey",
    "hey.",
    "so",
    "so.",
    "the end",
    "the end.",
    "you're going to be here.",
    "you're going to be here",
    "...",
    "",
}


def _filter_hallucination(text: str) -> str:
    """Return empty string if text is a known Whisper hallucination on silence."""
    stripped = text.strip().lower()
    if stripped in _WHISPER_HALLUCINATION_PATTERNS:
        log.info("Filtered Whisper hallucination: %r", text.strip())
        return ""
    return text.strip()


# --- Pydantic Request/Response Models ---


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    voice: bool = False  # When True, include audio_chunk SSE events with TTS audio


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., pattern=r"^(pending|in_progress|done|dismissed)$")


class TaskResponse(BaseModel):
    id: int
    title: str
    description: str
    source_type: str
    source_id: int
    priority: str
    due_date: Optional[str] = None
    status: str
    classification: Optional[str] = None
    dismissal_reason: str = ""
    dismissed_at: Optional[str] = None
    created_at: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    count: int


class UpdateStatusResponse(BaseModel):
    success: bool
    task_id: int
    status: str


class DismissTaskRequest(BaseModel):
    reason: str = ""


class RestoreTaskResponse(BaseModel):
    success: bool
    task_id: int


class DismissedTaskResponse(BaseModel):
    id: int
    title: str
    dismissal_reason: str
    dismissed_at: Optional[str] = None
    source_type: str = ""
    priority: str = "medium"


class DismissedTaskListResponse(BaseModel):
    tasks: list[DismissedTaskResponse]
    count: int


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    priority: str = Field("medium", pattern=r"^(high|medium|low)$")
    due_date: Optional[str] = None  # ISO 8601 YYYY-MM-DD
    goal_id: Optional[int] = None


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    priority: Optional[str] = Field(None, pattern=r"^(high|medium|low)$")
    due_date: Optional[str] = None  # ISO 8601 YYYY-MM-DD or "" to clear
    status: Optional[str] = Field(
        None, pattern=r"^(pending|in_progress|done|dismissed)$"
    )
    goal_id: Optional[int] = None


class SyncInfo(BaseModel):
    source: str
    last_sync: Optional[str] = None
    last_count: int
    last_status: str


class StatusResponse(BaseModel):
    emails: int
    events: int
    pending_tasks: int
    syncs: list[SyncInfo]
    model: str
    model_loaded: bool


class ProfileResponse(BaseModel):
    display_name: str
    email_address: str
    top_contacts: list[dict]
    top_topics: list[str]
    active_hours: dict[str, int]
    avg_response_time_min: float
    email_volume_daily: float
    summary: str
    updated_at: Optional[str] = None


class SyncResponse(BaseModel):
    mail_synced: int
    mail_filtered: int
    events_synced: int
    profile_updated: bool
    needs_onboarding: bool = False


class ExtractResponse(BaseModel):
    tasks_extracted: int


class PowerStateResponse(BaseModel):
    on_battery: bool
    battery_percent: int | None = None
    thermal_state: int = 0
    memory_pressure_pct: float = 0.0
    loaded_models: int = 0


class HealthResponse(BaseModel):
    status: str
    version: str
    commit: str
    power: PowerStateResponse | None = None


class TranscribeResponse(BaseModel):
    text: str


class OnboardingRequest(BaseModel):
    response: str = Field("", max_length=4096)


class OnboardingStatusResponse(BaseModel):
    needs_onboarding: bool
    onboarding_step: int
    onboarding_completed: bool


class ResetResponse(BaseModel):
    success: bool
    message: str


class HardwareInfoResponse(BaseModel):
    chip: str
    ram_gb: int
    gpu_cores: int


class ModelInfoResponse(BaseModel):
    model_id: str
    size_gb: float
    params: str
    quant: str
    downloads: int
    is_downloaded: bool = False
    download_status: str = "not_downloaded"  # complete | partial | not_downloaded


class ModelRecommendationResponse(BaseModel):
    assistant: str
    filter: str
    reasoning: str


class AppleModelInfoResponse(BaseModel):
    available: bool
    reason: Optional[str] = None  # None when available, message when not


class ModelStatusResponse(BaseModel):
    setup_completed: bool
    current_assistant: str
    current_filter: str
    hardware: HardwareInfoResponse
    apple_model: Optional[AppleModelInfoResponse] = None


class AvailableModelsResponse(BaseModel):
    hardware: HardwareInfoResponse
    compatible_models: list[ModelInfoResponse]
    recommended: ModelRecommendationResponse
    apple_model: Optional[AppleModelInfoResponse] = None


class ModelSelectRequest(BaseModel):
    assistant_model: str = Field(..., min_length=1)
    filter_model: str = Field(..., min_length=1)


class ModelSelectResponse(BaseModel):
    success: bool
    message: str


# --- Config Pydantic Models ---


class LLMConfigResponse(BaseModel):
    model: str
    filter_model: str
    max_tokens: int
    temperature: float
    top_p: float
    context_budget_tokens: int


class VoiceConfigResponse(BaseModel):
    enabled: bool
    tts_model: str
    tts_voice: str
    stt_model: str
    sample_rate: int


class PowerConfigResponse(BaseModel):
    enabled: bool
    battery_pause_threshold: int
    battery_defer_heavy_threshold: int
    thermal_pause_threshold: int
    thermal_defer_heavy_threshold: int
    model_idle_timeout_minutes: int


class MailConfigResponse(BaseModel):
    mailboxes: list[str]
    batch_size: int
    sync_interval_minutes: int
    initial_sync_months: int
    deep_sync_max_months: int


class CalendarConfigResponse(BaseModel):
    sync_window_past_days: int
    sync_window_future_days: int
    sync_interval_minutes: int


class AgentsConfigResponse(BaseModel):
    enabled: bool
    routing_enabled: bool
    max_execution_seconds: int


class GoalsConfigResponse(BaseModel):
    strategy_interval_hours: int
    daily_review_hour: int
    max_strategies_per_run: int
    plan_horizon_days: int


class ConfigResponse(BaseModel):
    llm: LLMConfigResponse
    voice: VoiceConfigResponse
    power: PowerConfigResponse
    mail: MailConfigResponse
    calendar: CalendarConfigResponse
    agents: AgentsConfigResponse
    goals: GoalsConfigResponse


class ConfigUpdateRequest(BaseModel):
    llm: Optional[dict] = None
    voice: Optional[dict] = None
    power: Optional[dict] = None
    mail: Optional[dict] = None
    calendar: Optional[dict] = None
    agents: Optional[dict] = None
    goals: Optional[dict] = None


# --- Goal Pydantic Models ---


class GoalProgressResponse(BaseModel):
    id: int
    goal_id: int
    note: str
    source: str
    created_at: Optional[str] = None


class GoalResponse(BaseModel):
    id: int
    title: str
    tier: str
    description: str
    category: str
    parent_id: Optional[int] = None
    status: str
    priority: str
    target_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    progress: list[GoalProgressResponse] = []
    children: list[dict] = []
    strategies: list[dict] = []
    tasks: list[dict] = []


class GoalListResponse(BaseModel):
    goals: list[GoalResponse]
    count: int


class GoalRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    tier: str = Field(..., pattern=r"^(long_term|mid_term|short_term)$")
    description: str = ""
    category: str = ""
    parent_id: Optional[int] = None
    priority: str = Field("medium", pattern=r"^(high|medium|low)$")
    target_date: Optional[str] = None


class GoalUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = Field(None, pattern=r"^(high|medium|low)$")
    target_date: Optional[str] = None


class GoalStatusRequest(BaseModel):
    status: str = Field(
        ..., pattern=r"^(active|paused|completed|abandoned)$"
    )


class GoalProgressRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)
    source: str = Field("user", pattern=r"^(user|sync|review|chat)$")


class PlanAcceptRequest(BaseModel):
    plan_json: str = Field(..., min_length=1)


class ReviewRespondRequest(BaseModel):
    review_id: int
    response: str = Field(..., min_length=1, max_length=4096)


class ReviewStatusResponse(BaseModel):
    due: bool
    last_review_date: Optional[str] = None


class ReviewSummaryResponse(BaseModel):
    summary: str


class GoalChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)


class TaskChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)


class ModelDownloadRequest(BaseModel):
    model_id: str = Field(..., min_length=1)


class SessionStateResponse(BaseModel):
    """Snapshot of the current session state for the UI.

    ``phase`` is the server's authoritative checkpoint.
    The UI should derive everything from this single field.
    """
    phase: str  # server checkpoint (ready | syncing | onboarding | operational | ...)
    messages: list[dict] = []  # Replay of onboarding history [{role, content}]
    needs_response: bool = False  # True if waiting for user input in onboarding
    stats: Optional[dict] = None


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize config, store, and bootstrap state on startup."""
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(config.db_path)
    app.state.store = store
    app.state.config = config

    # Initialize bootstrap state machine
    from giva.bootstrap import BootstrapNotifier, BootstrapState, run_bootstrap

    app.state.bootstrap = BootstrapState.load()
    app.state.bootstrap_notifier = BootstrapNotifier()
    app.state.bootstrap_task = None

    # Store event loop reference for background threads (scheduler broadcasting)
    app.state._event_loop = asyncio.get_event_loop()
    app.state.session_queues = []
    app.state.scheduler = None

    # Discover pluggable agents
    from giva.agents.registry import registry as agent_registry

    agent_count = agent_registry.discover(db_path=config.db_path)
    log.info("Agent registry: %d agents discovered", agent_count)

    # Initialize agent execution queue
    from giva.agents.queue import AgentQueue

    def _broadcast_to_sessions(event: dict) -> None:
        """Push an event to all session queues from the agent queue thread."""
        queues: list = getattr(app.state, "session_queues", [])
        loop = getattr(app.state, "_event_loop", None)
        if not loop or not queues:
            return
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

    agent_queue = AgentQueue(store, config, _llm_lock, _broadcast_to_sessions)
    agent_queue.start()
    app.state.agent_queue = agent_queue

    log.info(
        "Server started — DB: %s, bootstrap: %s (ready=%s, operational=%s)",
        config.db_path, app.state.bootstrap.checkpoint,
        app.state.bootstrap.is_ready, app.state.bootstrap.is_operational,
    )

    # Auto-resume bootstrap if not ready
    if not app.state.bootstrap.is_ready:
        log.info(
            "Bootstrap not ready (checkpoint=%s) — launching run_bootstrap task",
            app.state.bootstrap.checkpoint,
        )
        app.state.bootstrap_task = asyncio.create_task(run_bootstrap(app))
    elif app.state.bootstrap.is_operational:
        # Server restarted while already operational — start scheduler
        log.info("Already operational (checkpoint=%s) — starting scheduler",
                 app.state.bootstrap.checkpoint)
        _start_scheduler(app)
    else:
        log.info("Ready but not operational — waiting for session stream")

    yield

    # Stop agent queue on shutdown
    if hasattr(app.state, "agent_queue"):
        app.state.agent_queue.stop()

    # Stop scheduler on shutdown
    if app.state.scheduler:
        app.state.scheduler.stop()

    # Cancel bootstrap task on shutdown
    if app.state.bootstrap_task and not app.state.bootstrap_task.done():
        app.state.bootstrap_task.cancel()
        try:
            await app.state.bootstrap_task
        except asyncio.CancelledError:
            pass

    # Shut down MCP event loop (if it was started)
    try:
        from giva.agents.mcp_agent.lifecycle import shutdown_mcp_loop

        shutdown_mcp_loop()
    except ImportError:
        pass  # mcp_agent package not installed or MCP not used

    log.info("Giva server shutting down")


def _start_scheduler(app) -> None:
    """Start the background sync scheduler (called once operational)."""
    if app.state.scheduler is not None:
        return  # Already running
    from giva.sync.scheduler import SyncScheduler
    scheduler = SyncScheduler(
        app.state.store, app.state.config, app=app, llm_lock=_llm_lock,
        agent_queue=getattr(app.state, "agent_queue", None),
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("Background scheduler started")


# --- App ---

app = FastAPI(
    title="Giva API",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:7483",
        "http://localhost:7483",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- SSE Bridge ---


def _model_starts_in_think() -> bool:
    """Check if the current assistant model's chat template starts in think mode.

    Some models (e.g. Qwen3-Next-*-Thinking) include ``<think>`` in the
    generation prompt, so the model starts outputting thinking content
    directly without emitting a ``<think>`` tag.
    """
    from giva.llm.engine import manager

    config = load_config()
    model_id = config.llm.model
    if not manager.is_loaded(model_id):
        # Model not loaded yet — check name heuristic
        return "thinking" in model_id.lower()

    _, tokenizer = manager._get(model_id)
    try:
        prompt = tokenizer.apply_chat_template(
            [{"role": "user", "content": "hi"}],
            tokenize=False,
            add_generation_prompt=True,
        )
        return prompt.rstrip().endswith("<think>")
    except Exception:
        return "thinking" in model_id.lower()


class _ThinkParser:
    """Stateful parser that splits streamed LLM output into thinking vs. response.

    Qwen3 models emit <think>...</think> blocks before the actual answer.
    This parser routes tokens to either "thinking" or "token" SSE events.
    Handles tags split across token boundaries via a small buffer.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self, *, start_in_think: bool = False):
        self._in_think = start_in_think
        self._buf = ""  # Buffer for partial tag matching

    def feed(self, text: str) -> list[tuple[str, str]]:
        """Feed a chunk of text, return list of (event_type, text) pairs."""
        self._buf += text
        events: list[tuple[str, str]] = []

        while self._buf:
            if not self._in_think:
                # Look for <think> open tag
                idx = self._buf.find(self._OPEN)
                if idx >= 0:
                    # Emit any text before the tag as a token
                    before = self._buf[:idx]
                    if before:
                        events.append(("token", before))
                    self._buf = self._buf[idx + len(self._OPEN):]
                    self._in_think = True
                    continue
                # Check if end of buffer could be the start of "<think>"
                # e.g. buffer ends with "<", "<t", "<th", etc.
                partial_match = self._partial_tag_len(self._buf, self._OPEN)
                if partial_match > 0:
                    # Emit everything except the potential partial tag
                    safe = self._buf[:-partial_match]
                    if safe:
                        events.append(("token", safe))
                    self._buf = self._buf[-partial_match:]
                    break  # Wait for more input
                else:
                    # No tag in sight — emit all
                    events.append(("token", self._buf))
                    self._buf = ""
            else:
                # Inside <think> — look for </think> close tag
                idx = self._buf.find(self._CLOSE)
                if idx >= 0:
                    before = self._buf[:idx]
                    if before:
                        events.append(("thinking", before))
                    self._buf = self._buf[idx + len(self._CLOSE):]
                    self._in_think = False
                    # Strip leading whitespace after </think>
                    self._buf = self._buf.lstrip("\n")
                    continue
                partial_match = self._partial_tag_len(self._buf, self._CLOSE)
                if partial_match > 0:
                    safe = self._buf[:-partial_match]
                    if safe:
                        events.append(("thinking", safe))
                    self._buf = self._buf[-partial_match:]
                    break
                else:
                    events.append(("thinking", self._buf))
                    self._buf = ""

        return events

    def flush(self) -> list[tuple[str, str]]:
        """Flush any remaining buffered text at end of stream."""
        if not self._buf:
            return []
        event_type = "thinking" if self._in_think else "token"
        remaining = self._buf
        self._buf = ""
        return [(event_type, remaining)]

    @staticmethod
    def _partial_tag_len(text: str, tag: str) -> int:
        """Check if text ends with a partial prefix of tag. Return match length."""
        for length in range(min(len(tag) - 1, len(text)), 0, -1):
            if text[-length:] == tag[:length]:
                return length
        return 0


async def _sync_gen_to_sse(gen_fn, *args, **kwargs) -> AsyncGenerator[dict, None]:
    """Bridge a synchronous Generator[str, None, None] to async SSE events.

    Runs the generator in a thread pool (with LLM lock) and pushes tokens
    into an asyncio.Queue for non-blocking consumption.
    Parses <think>...</think> blocks into separate "thinking" events.
    Emits a ``model_loading`` event if the model needs to be loaded first.
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _run():
        # Signal the agent queue that user chat is active — the queue
        # consumer will wait for this to clear before starting the next job.
        aq = getattr(app.state, "agent_queue", None)
        if aq:
            aq.chat_active.set()
        try:
            # Notify the client if the model isn't loaded yet — this means
            # we'll spend 10-30s loading before any tokens arrive.
            from giva.llm.engine import manager

            config = load_config()
            if not manager.is_loaded(config.llm.model):
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"event": "model_loading", "data": config.llm.model},
                )

            # Some models (Qwen3-Next-*-Thinking) inject <think> in the prompt
            # template, so the model outputs thinking content without a <think> tag.
            parser = _ThinkParser(start_in_think=_model_starts_in_think())
            with _llm_lock:
                for token in gen_fn(*args, **kwargs):
                    for event_type, data in parser.feed(token):
                        loop.call_soon_threadsafe(
                            queue.put_nowait, {"event": event_type, "data": data}
                        )
                # Flush any remaining buffer
                for event_type, data in parser.flush():
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"event": event_type, "data": data}
                    )
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "done", "data": ""}
                )
        except Exception as e:
            log.exception("SSE generator error")
            loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "error", "data": str(e)}
            )
        finally:
            if aq:
                aq.chat_active.clear()

    future = loop.run_in_executor(None, _run)

    while True:
        item = await queue.get()
        yield item
        if item["event"] in ("done", "error"):
            break

    await future


async def _sync_gen_to_sse_with_voice(
    gen_fn, config, *args, **kwargs
) -> AsyncGenerator[dict, None]:
    """Like _sync_gen_to_sse but also synthesizes TTS audio per sentence.

    Emits both "token" events (text) and "audio_chunk" events (base64 WAV).
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _run():
        import re

        sentence_re = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")

        # Lazy-load TTS
        from giva.audio.tts import TTSEngine

        tts = TTSEngine(config.voice)

        def _synth_sentence(text: str):
            """Synthesize a sentence and push audio_chunk event."""
            try:
                with _voice_lock:
                    audio, sr = tts.synthesize(text)
                if len(audio) > 0:
                    # Encode as WAV bytes then base64
                    import soundfile as sf

                    buf = io.BytesIO()
                    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"event": "audio_chunk", "data": b64},
                    )
            except Exception as e:
                log.warning("TTS synthesis error in SSE: %s", e)

        sentence_buffer = ""
        # Signal the agent queue that user chat is active
        aq = getattr(app.state, "agent_queue", None)
        if aq:
            aq.chat_active.set()
        try:
            parser = _ThinkParser(start_in_think=_model_starts_in_think())
            with _llm_lock:
                for token in gen_fn(*args, **kwargs):
                    for event_type, data in parser.feed(token):
                        loop.call_soon_threadsafe(
                            queue.put_nowait, {"event": event_type, "data": data}
                        )
                        # Only buffer response tokens for TTS (not thinking)
                        if event_type == "token":
                            sentence_buffer += data
                            parts = sentence_re.split(sentence_buffer)
                            parts = [p for p in parts if p]
                            if len(parts) > 1:
                                for sentence in parts[:-1]:
                                    sentence = sentence.strip()
                                    if sentence:
                                        _synth_sentence(sentence)
                                sentence_buffer = parts[-1]

                # Flush parser
                for event_type, data in parser.flush():
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"event": event_type, "data": data}
                    )
                    if event_type == "token":
                        sentence_buffer += data

                # Synthesize remainder
                remainder = sentence_buffer.strip()
                if remainder:
                    _synth_sentence(remainder)

                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "done", "data": ""}
                )
        except Exception as e:
            log.exception("SSE generator error (voice)")
            loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "error", "data": str(e)}
            )
        finally:
            if aq:
                aq.chat_active.clear()

    future = loop.run_in_executor(None, _run)

    while True:
        item = await queue.get()
        yield item
        if item["event"] in ("done", "error"):
            break

    await future


# --- Routes ---


@app.get("/api/health")
async def health() -> HealthResponse:
    """Health check — lightweight, no DB or model access."""
    power = None
    try:
        from giva.llm.engine import manager
        from giva.utils.power import get_power_state

        ps = get_power_state()
        power = PowerStateResponse(
            on_battery=ps.on_battery,
            battery_percent=ps.battery_percent,
            thermal_state=ps.thermal_state,
            memory_pressure_pct=round(ps.memory_pressure_pct, 1),
            loaded_models=len(manager.loaded_models()),
        )
    except Exception:
        pass
    return HealthResponse(
        status="ok", version=__version__, commit=_GIT_COMMIT, power=power,
    )


@app.get("/api/status")
async def status(request: Request) -> StatusResponse:
    """System stats: email/event/task counts, sync times, model status."""
    store: Store = request.app.state.store
    config = request.app.state.config

    stats = store.get_stats()

    from giva.llm.engine import is_loaded

    return StatusResponse(
        emails=stats["emails"],
        events=stats["events"],
        pending_tasks=stats["pending_tasks"],
        syncs=[
            SyncInfo(
                source=s["source"],
                last_sync=s["last_sync"],
                last_count=s["last_count"],
                last_status=s["last_status"],
            )
            for s in stats["syncs"]
        ],
        model=config.llm.model,
        model_loaded=is_loaded(),
    )


@app.get("/api/config")
async def get_config(request: Request) -> ConfigResponse:
    """Return current configuration (all sections)."""
    config = request.app.state.config
    return ConfigResponse(
        llm=LLMConfigResponse(
            model=config.llm.model,
            filter_model=config.llm.filter_model,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
            top_p=config.llm.top_p,
            context_budget_tokens=config.llm.context_budget_tokens,
        ),
        voice=VoiceConfigResponse(
            enabled=config.voice.enabled,
            tts_model=config.voice.tts_model,
            tts_voice=config.voice.tts_voice,
            stt_model=config.voice.stt_model,
            sample_rate=config.voice.sample_rate,
        ),
        power=PowerConfigResponse(
            enabled=config.power.enabled,
            battery_pause_threshold=config.power.battery_pause_threshold,
            battery_defer_heavy_threshold=config.power.battery_defer_heavy_threshold,
            thermal_pause_threshold=config.power.thermal_pause_threshold,
            thermal_defer_heavy_threshold=config.power.thermal_defer_heavy_threshold,
            model_idle_timeout_minutes=config.power.model_idle_timeout_minutes,
        ),
        mail=MailConfigResponse(
            mailboxes=config.mail.mailboxes,
            batch_size=config.mail.batch_size,
            sync_interval_minutes=config.mail.sync_interval_minutes,
            initial_sync_months=config.mail.initial_sync_months,
            deep_sync_max_months=config.mail.deep_sync_max_months,
        ),
        calendar=CalendarConfigResponse(
            sync_window_past_days=config.calendar.sync_window_past_days,
            sync_window_future_days=config.calendar.sync_window_future_days,
            sync_interval_minutes=config.calendar.sync_interval_minutes,
        ),
        agents=AgentsConfigResponse(
            enabled=config.agents.enabled,
            routing_enabled=config.agents.routing_enabled,
            max_execution_seconds=config.agents.max_execution_seconds,
        ),
        goals=GoalsConfigResponse(
            strategy_interval_hours=config.goals.strategy_interval_hours,
            daily_review_hour=config.goals.daily_review_hour,
            max_strategies_per_run=config.goals.max_strategies_per_run,
            plan_horizon_days=config.goals.plan_horizon_days,
        ),
    )


@app.put("/api/config")
async def update_config(request: Request, body: ConfigUpdateRequest):
    """Update configuration and persist to user config file.

    Only provided sections are updated; omitted sections are left unchanged.
    The server config is reloaded after writing so changes take effect immediately.
    """
    from giva.config import save_config, load_config

    updates = {}
    if body.llm is not None:
        updates["llm"] = body.llm
    if body.voice is not None:
        updates["voice"] = body.voice
    if body.power is not None:
        updates["power"] = body.power
    if body.mail is not None:
        updates["mail"] = body.mail
    if body.calendar is not None:
        updates["calendar"] = body.calendar
    if body.agents is not None:
        updates["agents"] = body.agents
    if body.goals is not None:
        updates["goals"] = body.goals

    if not updates:
        raise HTTPException(status_code=400, detail="No config sections provided")

    save_config(updates)
    request.app.state.config = load_config()
    log.info("Config updated: sections=%s", list(updates.keys()))

    return {"success": True, "updated_sections": list(updates.keys())}


@app.get("/api/profile")
async def profile(request: Request) -> ProfileResponse:
    """User profile with summary text."""
    store: Store = request.app.state.store

    from giva.intelligence.profile import get_profile_summary

    p = store.get_profile()
    if not p or not p.email_address:
        raise HTTPException(status_code=404, detail="No profile built yet. Run /api/sync first.")

    summary = get_profile_summary(store)

    return ProfileResponse(
        display_name=p.display_name,
        email_address=p.email_address,
        top_contacts=p.top_contacts,
        top_topics=p.top_topics,
        active_hours=p.active_hours,
        avg_response_time_min=p.avg_response_time_min,
        email_volume_daily=p.email_volume_daily,
        summary=summary,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


def _task_response(t) -> TaskResponse:
    """Convert a Task dataclass to a TaskResponse."""
    return TaskResponse(
        id=t.id,
        title=t.title,
        description=t.description,
        source_type=t.source_type,
        source_id=t.source_id,
        priority=t.priority,
        due_date=t.due_date.isoformat() if t.due_date else None,
        status=t.status,
        classification=t.classification,
        dismissal_reason=t.dismissal_reason or "",
        dismissed_at=t.dismissed_at.isoformat() if t.dismissed_at else None,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


@app.get("/api/tasks")
async def get_tasks(
    request: Request,
    status: Optional[str] = Query(None, pattern=r"^(pending|in_progress|done|dismissed)$"),
    limit: int = Query(50, ge=1, le=200),
) -> TaskListResponse:
    """List tasks, optionally filtered by status."""
    store: Store = request.app.state.store
    tasks = store.get_tasks(status=status, limit=limit)

    task_list = [_task_response(t) for t in tasks]

    return TaskListResponse(tasks=task_list, count=len(task_list))


@app.post("/api/tasks/{task_id}/status")
async def update_task_status(
    task_id: int,
    req: UpdateStatusRequest,
    request: Request,
) -> UpdateStatusResponse:
    """Update a task's status (done, dismissed, etc.)."""
    store: Store = request.app.state.store

    if req.status == "dismissed":
        success = store.dismiss_task(task_id, "Dismissed by user")
    else:
        success = store.update_task_status(task_id, req.status)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    # Auto-aggregate progress on linked goal when a task is completed
    if req.status == "done":
        try:
            from giva.intelligence.agents import aggregate_task_progress

            action = aggregate_task_progress(task_id, store)
            if action:
                event = {
                    "event": "agent_actions",
                    "data": json.dumps([action]),
                }
                for q in getattr(request.app.state, "session_queues", []):
                    try:
                        q.put_nowait(event)
                    except Exception:
                        pass
        except Exception as e:
            log.debug("Progress aggregation error for task %d: %s", task_id, e)

    return UpdateStatusResponse(success=True, task_id=task_id, status=req.status)


@app.post("/api/tasks")
async def create_task(
    req: TaskCreateRequest,
    request: Request,
) -> TaskResponse:
    """Create a new task manually."""
    from giva.db.models import Task as TaskModel

    store: Store = request.app.state.store

    due_date = None
    if req.due_date:
        try:
            from datetime import datetime

            due_date = datetime.fromisoformat(req.due_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid due_date format. Use YYYY-MM-DD."
            )

    task = TaskModel(
        title=req.title,
        description=req.description,
        source_type="manual",
        source_id=0,
        priority=req.priority,
        due_date=due_date,
        status="pending",
        goal_id=req.goal_id,
    )
    task_id = store.add_task(task)
    created = store.get_task(task_id)

    return _task_response(created)


@app.put("/api/tasks/{task_id}")
async def update_task(
    task_id: int,
    req: TaskUpdateRequest,
    request: Request,
) -> TaskResponse:
    """Update a task's fields (title, description, priority, due_date, status, goal_id)."""
    store: Store = request.app.state.store

    # Build update kwargs from non-None fields
    updates = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.description is not None:
        updates["description"] = req.description
    if req.priority is not None:
        updates["priority"] = req.priority
    if req.status is not None:
        updates["status"] = req.status
    if req.goal_id is not None:
        updates["goal_id"] = req.goal_id
    if req.due_date is not None:
        if req.due_date == "":
            updates["due_date"] = None
        else:
            try:
                from datetime import datetime

                updates["due_date"] = datetime.fromisoformat(req.due_date)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid due_date format. Use YYYY-MM-DD.",
                )

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    success = store.update_task(task_id, **updates)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    updated = store.get_task(task_id)
    return _task_response(updated)


@app.get("/api/tasks/dismissed")
async def get_dismissed_tasks(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
) -> DismissedTaskListResponse:
    """List recently dismissed tasks for the undo queue."""
    store: Store = request.app.state.store
    tasks = store.get_dismissed_tasks(limit=limit)
    return DismissedTaskListResponse(
        tasks=[
            DismissedTaskResponse(
                id=t.id,
                title=t.title,
                dismissal_reason=t.dismissal_reason or "",
                dismissed_at=t.dismissed_at.isoformat() if t.dismissed_at else None,
                source_type=t.source_type,
                priority=t.priority,
            )
            for t in tasks
        ],
        count=len(tasks),
    )


@app.post("/api/tasks/{task_id}/restore")
async def restore_task(task_id: int, request: Request) -> RestoreTaskResponse:
    """Restore a dismissed task back to pending."""
    store: Store = request.app.state.store
    success = store.restore_task(task_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Task not found or not dismissed",
        )
    return RestoreTaskResponse(success=True, task_id=task_id)


@app.post("/api/tasks/{task_id}/dismiss")
async def dismiss_task_endpoint(
    task_id: int,
    req: DismissTaskRequest,
    request: Request,
) -> UpdateStatusResponse:
    """Dismiss a task with an explicit reason."""
    store: Store = request.app.state.store
    success = store.dismiss_task(task_id, req.reason)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return UpdateStatusResponse(success=True, task_id=task_id, status="dismissed")


@app.post("/api/tasks/{task_id}/ai")
async def task_ai(task_id: int, request: Request):
    """Plan an agent-assisted approach to a task via the orchestrator.

    Creates a pending_confirmation job in the agent queue with the
    orchestrator's plan summary.  The client shows the plan and asks
    the user to approve before execution.
    """
    store: Store = request.app.state.store
    config = request.app.state.config
    aq = getattr(request.app.state, "agent_queue", None)

    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    query = f"Help me accomplish this task: {task.title}"
    if task.description:
        query += f"\nDetails: {task.description}"

    from giva.agents.registry import registry as agent_registry

    orch = agent_registry.get("orchestrator")
    if not orch:
        raise HTTPException(status_code=503, detail="Orchestrator agent not available")

    loop = asyncio.get_event_loop()

    # Plan under lock
    def _plan():
        with _llm_lock:
            return orch.plan_only(query, config)

    plan = await loop.run_in_executor(None, _plan)

    plan_summary = None
    if plan:
        from giva.agents.orchestrator.planner import format_plan_summary

        plan_summary = format_plan_summary(plan)

    from giva.agents.queue import AgentJob, make_job_id

    job = AgentJob(
        job_id=make_job_id(),
        agent_id="orchestrator",
        query=query,
        context={"params": {}, "query": query, "task_id": task_id},
        priority=0,
        status="pending_confirmation",
        source="task",
        task_id=task_id,
        plan_summary=plan_summary,
    )
    if aq:
        aq.enqueue(job)

    return {
        "job_id": job.job_id,
        "plan_summary": plan_summary,
        "agent_id": "orchestrator",
    }


@app.post("/api/tasks/{task_id}/chat")
async def task_chat(task_id: int, req: TaskChatRequest, request: Request):
    """Task-scoped chat: sends task context to LLM. Returns SSE stream.

    Messages are persisted scoped to this task (not mixed with global or goal chat).
    After the response stream, runs the post-chat agent pipeline to detect
    intents (create sub-task, complete task, progress, etc.).
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    from giva.intelligence.queries import handle_query

    # Build task context prefix — sent to LLM but NOT saved to DB
    task_context = (
        f"[Context: working on task '{task.title}' "
        f"(priority: {task.priority}, status: {task.status}). "
        f"Description: {task.description or 'N/A'}."
    )
    if task.due_date:
        task_context += f" Due: {task.due_date.isoformat()[:10]}."
    if task.goal_id:
        goal = store.get_goal(task.goal_id)
        if goal:
            task_context += f" Linked to goal: {goal.title}."
    task_context += (
        " You are a coordinator agent helping the user accomplish this task. "
        "Plan concrete steps, draft assets (emails, documents) for review, "
        "and report where deliverables are stored. Never send or publish "
        "anything without explicit user approval.]"
    )
    original_query = req.query

    async def event_generator():
        async for event in _sync_gen_to_sse(
            handle_query, original_query, store, config,
            task_id=task_id, context_prefix=task_context,
        ):
            yield event
        # Post-chat agents (after stream done, lock released)
        actions = await _run_post_chat(
            original_query, store, config, task_id=task_id,
        )
        if actions:
            yield {
                "event": "agent_actions",
                "data": json.dumps(actions),
            }

    return EventSourceResponse(event_generator())


@app.get("/api/tasks/{task_id}/messages")
async def task_messages(
    task_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
):
    """Retrieve persisted task chat messages."""
    store: Store = request.app.state.store

    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    msgs = store.get_task_messages(task_id, limit=limit)
    return {
        "messages": [
            {"role": m["role"], "content": m["content"], "created_at": m.get("created_at")}
            for m in msgs
        ],
        "count": len(msgs),
    }


@app.post("/api/sync")
async def sync(request: Request) -> SyncResponse:
    """Trigger full email + calendar sync. Long-running (~30s)."""
    store: Store = request.app.state.store
    config = request.app.state.config

    def _run_sync():
        from giva.intelligence.profile import update_profile
        from giva.sync.calendar import sync_calendar
        from giva.sync.mail import sync_mail_jxa

        with _llm_lock:
            mail_synced, mail_filtered = sync_mail_jxa(
                store, config.mail.mailboxes, config.mail.batch_size, config=config
            )

        events_synced = sync_calendar(
            store, config.calendar.sync_window_past_days, config.calendar.sync_window_future_days
        )

        profile_updated = False
        try:
            update_profile(store, config)
            profile_updated = True
        except Exception:
            pass

        return mail_synced, mail_filtered, events_synced, profile_updated

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_sync)
    mail_synced, mail_filtered, events_synced, profile_updated = result

    from giva.intelligence.onboarding import is_onboarding_needed

    return SyncResponse(
        mail_synced=mail_synced,
        mail_filtered=mail_filtered,
        events_synced=events_synced,
        profile_updated=profile_updated,
        needs_onboarding=is_onboarding_needed(store),
    )


@app.post("/api/extract")
async def extract(request: Request) -> ExtractResponse:
    """Trigger task extraction from unprocessed emails and events."""
    store: Store = request.app.state.store
    config = request.app.state.config

    def _run_extract():
        from giva.intelligence.tasks import extract_tasks

        with _llm_lock:
            return extract_tasks(store, config)

    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, _run_extract)

    return ExtractResponse(tasks_extracted=count)


# --- Post-Chat Agent Helper (shared by /chat and /goals/{id}/chat) ---

async def _run_post_chat(
    query: str,
    store: Store,
    config,
    goal_id: int | None = None,
    task_id: int | None = None,
) -> list[dict]:
    """Run post-chat agents in a thread with LLM lock.

    Args:
        goal_id: When set, scopes message retrieval and auto-links
            created tasks/objectives to this goal.  Conversation
            compression is skipped for goal-scoped chats.
        task_id: When set, scopes message retrieval to this task.
            Conversation compression is skipped for task-scoped chats.
    """
    loop = asyncio.get_event_loop()

    def _agents():
        actions: list[dict] = []
        # Get the last assistant response from the DB (scoped)
        recent = store.get_recent_messages(
            limit=2, goal_id=goal_id, task_id=task_id
        )
        response_text = ""
        for m in reversed(recent):
            if m["role"] == "assistant":
                response_text = m["content"]
                break

        if not response_text:
            return actions

        # Extract source references so the post-chat agent can link
        # created tasks to the email/event being discussed
        context_sources = None
        try:
            from giva.intelligence.context import retrieve_context_sources
            context_sources = retrieve_context_sources(query, store)
        except Exception:
            pass

        # 1. Run combined post-chat agent (intent + tagging + progress)
        try:
            from giva.intelligence.agents import run_post_chat_agent

            with _llm_lock:
                actions = run_post_chat_agent(
                    query, response_text, store, config,
                    goal_id=goal_id, task_id=task_id,
                    context_sources=context_sources,
                )
        except Exception as e:
            log.debug("Post-chat agent error: %s", e)

        # 2. Run conversation compressor if needed (global chat only)
        if goal_id is None and task_id is None:
            try:
                from giva.intelligence.context import maybe_compress_conversation

                with _llm_lock:
                    compressed = maybe_compress_conversation(store, config.llm)
                if compressed:
                    actions.append({"type": "conversation_compressed"})
            except Exception as e:
                log.debug("Conversation compressor error: %s", e)

        return actions

    return await loop.run_in_executor(None, _agents)


async def _check_agent_routing(
    query: str,
    store: Store,
    config,
    goal_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    """Check if the query should trigger an agent via the AgentQueue.

    Runs after the normal chat stream completes. If an agent is matched:
    - If requires_confirmation: enqueues as pending_confirmation, yields agent_confirm
    - Otherwise: enqueues for background execution (result comes via session stream)

    Yields SSE events (may yield nothing if no agent is needed).
    """
    if not config.agents.enabled or not config.agents.routing_enabled:
        return

    from giva.agents.registry import registry as agent_registry

    if not agent_registry.has_agents():
        return

    loop = asyncio.get_event_loop()

    # Check if the assistant response contains the [NEEDS_AGENT] marker
    recent = store.get_recent_messages(limit=2, goal_id=goal_id)
    response_text = ""
    for m in reversed(recent):
        if m["role"] == "assistant":
            response_text = m["content"]
            break

    has_marker = "[NEEDS_AGENT]" in response_text

    # Only route if the marker is present or keyword pre-filter matches
    from giva.agents.router import keyword_prefilter

    manifests = agent_registry.list_manifests()
    candidates = keyword_prefilter(query, manifests)

    if not has_marker and not candidates:
        return

    # Run the LLM router (under lock) to decide which agent to use
    def _route_query():
        from giva.agents.router import route_query

        with _llm_lock:
            return route_query(query, config)

    route = await loop.run_in_executor(None, _route_query)
    if route is None:
        return

    agent_id, params = route
    agent = agent_registry.get(agent_id)
    if not agent:
        return

    # Get the agent queue (may not be available during tests)
    aq = getattr(app.state, "agent_queue", None)

    from giva.agents.queue import AgentJob, make_job_id

    context = {"params": params, "query": query}
    if goal_id is not None:
        context["goal_id"] = goal_id

    if agent.manifest.requires_confirmation:
        # Build a confirmation message (includes orchestrator plan if applicable)
        confirm_message = (
            f"I can use the {agent.manifest.name} to help with this. "
            f"Would you like me to proceed?"
        )
        plan_summary = None
        if agent_id == "orchestrator":
            def _plan():
                with _llm_lock:
                    return agent.plan_only(query, config)

            try:
                plan = await loop.run_in_executor(None, _plan)
                if plan:
                    from giva.agents.orchestrator.planner import format_plan_summary
                    confirm_message = format_plan_summary(plan)
                    plan_summary = confirm_message
                else:
                    confirm_message = (
                        "I'd like to break this into steps, but couldn't "
                        "create a plan. Would you like me to try anyway?"
                    )
            except Exception as exc:
                log.debug("Orchestrator plan_only failed: %s", exc)

        # Enqueue as pending_confirmation — waits for user approval
        job = AgentJob(
            job_id=make_job_id(),
            agent_id=agent_id,
            query=query,
            context=context,
            priority=0,
            status="pending_confirmation",
            source="goal" if goal_id else "chat",
            goal_id=goal_id,
            plan_summary=plan_summary,
        )
        if aq:
            aq.enqueue(job)

        yield {"event": "agent_confirm", "data": json.dumps({
            "job_id": job.job_id,
            "agent_id": agent_id,
            "agent_name": agent.manifest.name,
            "params": params,
            "message": confirm_message,
        })}
    else:
        # Non-confirmation agent: enqueue for immediate background execution.
        # The result will be delivered via the session stream as
        # agent_job_completed / agent_job_failed events.
        job = AgentJob(
            job_id=make_job_id(),
            agent_id=agent_id,
            query=query,
            context=context,
            priority=0,
            source="goal" if goal_id else "chat",
            goal_id=goal_id,
        )
        if aq:
            aq.enqueue(job)

        yield {"event": "agent_queued", "data": json.dumps({
            "job_id": job.job_id,
            "agent_id": agent_id,
            "agent_name": agent.manifest.name,
        })}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    """Streaming chat via SSE. Returns token-by-token LLM response.

    After the response stream completes, runs the post-chat agent pipeline
    (intent detection, progress tracking, conversation compression) using the
    filter model and emits ``agent_actions`` SSE events for UI feedback.

    If pluggable agents are enabled, the response is also checked for the
    [NEEDS_AGENT] marker. If found, the agent router runs and emits an
    ``agent_result`` SSE event with the matched agent's output.

    When voice=true, also emits "audio_chunk" events containing base64-encoded
    WAV audio for each sentence (synthesized via Qwen3-TTS).
    """
    store: Store = request.app.state.store
    config = request.app.state.config
    query_text = req.query

    from giva.intelligence.queries import handle_query

    if req.voice and config.voice.enabled:
        async def event_generator():
            async for event in _sync_gen_to_sse_with_voice(
                handle_query, config, req.query, store, config
            ):
                yield event
            # Post-chat agents (after stream done, lock released)
            actions = await _run_post_chat(query_text, store, config)
            if actions:
                yield {
                    "event": "agent_actions",
                    "data": json.dumps(actions),
                }
            # Agent routing check (after response is complete)
            async for event in _check_agent_routing(query_text, store, config):
                yield event
    else:
        async def event_generator():
            async for event in _sync_gen_to_sse(handle_query, req.query, store, config):
                yield event
            # Post-chat agents (after stream done, lock released)
            actions = await _run_post_chat(query_text, store, config)
            if actions:
                yield {
                    "event": "agent_actions",
                    "data": json.dumps(actions),
                }
            # Agent routing check (after response is complete)
            async for event in _check_agent_routing(query_text, store, config):
                yield event

    return EventSourceResponse(event_generator())


@app.get("/api/suggest")
async def suggest(request: Request):
    """Streaming proactive suggestions via SSE."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.proactive import get_suggestions

    async def event_generator():
        async for event in _sync_gen_to_sse(get_suggestions, store, config):
            yield event

    return EventSourceResponse(event_generator())


# --- Pluggable Agent Endpoints ---


@app.get("/api/agents")
async def list_agents(request: Request):
    """List all available agents with their manifests."""
    from giva.agents.registry import registry as agent_registry

    manifests = agent_registry.list_manifests()
    return {
        "agents": [
            {
                "agent_id": m.agent_id,
                "name": m.name,
                "description": m.description,
                "examples": m.examples,
                "model_tier": m.model_tier,
                "supports_streaming": m.supports_streaming,
                "requires_confirmation": m.requires_confirmation,
                "version": m.version,
            }
            for m in manifests
        ],
        "count": len(manifests),
    }


@app.post("/api/agents/{agent_id}/execute")
async def execute_agent_endpoint(
    agent_id: str, req: ChatRequest, request: Request
):
    """Directly invoke a specific agent (bypasses routing)."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.agents.registry import registry as agent_registry
    from giva.agents.router import execute_agent

    agent = agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    loop = asyncio.get_event_loop()

    def _run():
        import time

        context = {"params": {}, "query": req.query}
        start = time.monotonic()
        # Agents with model_tier="none" (e.g. MCP) don't use the local LLM.
        if agent.manifest.model_tier == "none":
            result = execute_agent(agent_id, req.query, context, store, config)
        else:
            with _llm_lock:
                result = execute_agent(agent_id, req.query, context, store, config)
        duration_ms = int((time.monotonic() - start) * 1000)
        store.log_agent_execution(
            agent_id, req.query, {}, result.success,
            result.output[:500], result.artifacts,
            result.error or "", duration_ms,
        )
        return result

    result = await loop.run_in_executor(None, _run)

    async def event_generator():
        yield {"event": "token", "data": result.output}
        if result.actions:
            yield {"event": "agent_actions", "data": json.dumps(result.actions)}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


@app.get("/api/agents/history")
async def agent_history(
    request: Request,
    agent_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get recent agent execution history."""
    store: Store = request.app.state.store
    executions = store.get_agent_executions(agent_id=agent_id, limit=limit)
    return {"executions": executions, "count": len(executions)}


# ---------------------------------------------------------------------------
# Agent Queue endpoints
# ---------------------------------------------------------------------------


class AgentConfirmRequest(BaseModel):
    job_id: str


@app.get("/api/agents/queue")
async def list_agent_queue(
    request: Request,
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """List agent queue jobs, optionally filtered by status.

    Returns most recent first. Active + recent completed jobs.
    """
    aq = getattr(request.app.state, "agent_queue", None)
    if aq is None:
        return {"jobs": [], "count": 0}

    jobs = aq.list_jobs(status=status, limit=limit)
    return {
        "jobs": [j.to_dict() for j in jobs],
        "count": len(jobs),
        "active_count": aq.active_count,
    }


@app.post("/api/agents/confirm")
async def confirm_agent_job(req: AgentConfirmRequest, request: Request):
    """Confirm a pending_confirmation agent job and push it to the execution queue."""
    aq = getattr(request.app.state, "agent_queue", None)
    if aq is None:
        raise HTTPException(status_code=503, detail="Agent queue not available")

    if aq.confirm(req.job_id):
        return {"status": "confirmed", "job_id": req.job_id}
    raise HTTPException(
        status_code=404,
        detail=f"Job '{req.job_id}' not found or not pending confirmation",
    )


@app.post("/api/agents/queue/{job_id}/cancel")
async def cancel_agent_job(job_id: str, request: Request):
    """Cancel a queued or pending_confirmation agent job.

    Running jobs cannot be cancelled.
    """
    aq = getattr(request.app.state, "agent_queue", None)
    if aq is None:
        raise HTTPException(status_code=503, detail="Agent queue not available")

    if aq.cancel(job_id):
        return {"status": "cancelled", "job_id": job_id}
    raise HTTPException(
        status_code=404,
        detail=f"Job '{job_id}' not found or not cancellable (may be running)",
    )


@app.get("/api/agents/queue/{job_id}")
async def get_agent_job(job_id: str, request: Request):
    """Get details for a specific agent queue job."""
    aq = getattr(request.app.state, "agent_queue", None)
    if aq is None:
        raise HTTPException(status_code=503, detail="Agent queue not available")

    job = aq.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job.to_dict()


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


@app.get("/api/onboarding/status")
async def onboarding_status(request: Request) -> OnboardingStatusResponse:
    """Check if onboarding is needed."""
    store: Store = request.app.state.store

    from giva.intelligence.onboarding import is_onboarding_needed

    profile = store.get_profile()
    pd = profile.profile_data if profile else {}

    return OnboardingStatusResponse(
        needs_onboarding=is_onboarding_needed(store),
        onboarding_step=pd.get("onboarding_step", 0),
        onboarding_completed=pd.get("onboarding_completed", False),
    )


@app.post("/api/onboarding/start")
async def onboarding_start(request: Request):
    """Start the onboarding interview. Returns SSE stream with first question."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.onboarding import start_onboarding

    async def event_generator():
        async for event in _sync_gen_to_sse(start_onboarding, store, config):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/onboarding/respond")
async def onboarding_respond(req: OnboardingRequest, request: Request):
    """Continue the onboarding interview with user's response. Returns SSE stream."""
    store: Store = request.app.state.store
    config = request.app.state.config

    if not req.response.strip():
        raise HTTPException(status_code=400, detail="Response cannot be empty.")

    from giva.intelligence.onboarding import continue_onboarding

    async def event_generator():
        async for event in _sync_gen_to_sse(
            continue_onboarding, req.response, store, config
        ):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/reset")
async def reset(request: Request) -> ResetResponse:
    """Full tabula rasa: clear DB, caches, user config, and roll back bootstrap.

    After this, the app should restart the daemon.  On restart, bootstrap
    re-runs from "unknown": it finds the default model already downloaded,
    then parks at "awaiting_model_selection" so the user can re-pick models.

    Downloaded HuggingFace models are preserved (expensive to re-download).
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    # 1. Stop scheduler if running
    if request.app.state.scheduler:
        request.app.state.scheduler.stop()
        request.app.state.scheduler = None

    # 2. Roll checkpoint to "unknown" FIRST — this is the critical mutation.
    #    If anything below crashes, the daemon restart still re-enters bootstrap
    #    instead of resuming as operational with stale data.
    bootstrap = request.app.state.bootstrap
    log.info("Reset: checkpoint %s → unknown", bootstrap.checkpoint)
    bootstrap.advance("unknown")

    # 3. Clear all DB data (handles corrupt DB by deleting + recreating)
    store.reset_all_data()
    log.info("Reset: DB data cleared")

    # 4. Delete model and benchmark caches
    from pathlib import Path

    cache_files = [
        config.data_dir / "model_cache.json",
        config.data_dir / "benchmark_cache.json",
    ]
    for cache_file in cache_files:
        try:
            cache_file.unlink(missing_ok=True)
        except Exception as e:
            log.warning("Reset: failed to delete %s: %s", cache_file, e)

    # 5. Delete user config — forces model selection on next launch.
    #    Downloaded model files are preserved (expensive to re-download);
    #    the user picks which ones to use via the model setup UI.
    user_config = Path("~/.config/giva/config.toml").expanduser()
    existed = user_config.exists()
    try:
        user_config.unlink(missing_ok=True)
    except Exception as e:
        log.error("Reset: failed to delete user config: %s", e)

    if user_config.exists():
        log.error("Reset: user config still exists after unlink!")
    else:
        log.info("Reset: user config deleted (existed=%s)", existed)

    # 6. Reload config (falls back to defaults since user config is gone)
    from giva.config import load_config

    request.app.state.config = load_config()
    log.info(
        "Reset: config reloaded — model=%s filter=%s",
        request.app.state.config.llm.model,
        request.app.state.config.llm.filter_model,
    )

    return ResetResponse(
        success=True,
        message="All data cleared. Restart to re-sync and re-onboard.",
    )


@app.post("/api/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)) -> TranscribeResponse:
    """Transcribe an audio file (WAV/MP3) to text using Whisper MLX.

    Accepts multipart file upload. Returns transcribed text.
    """
    config = request.app.state.config

    # Read uploaded audio
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    def _run_transcribe():
        import tempfile
        from pathlib import Path

        stt = _get_stt_engine(config.voice)
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            with _voice_lock:
                return stt.transcribe_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _run_transcribe)

    # Filter common Whisper hallucinations on silence
    text = _filter_hallucination(text)

    return TranscribeResponse(text=text)


@app.post("/api/transcribe/stream")
async def transcribe_stream(request: Request, file: UploadFile = File(...)):
    """Transcribe audio with SSE streaming of results.

    Like ``/api/transcribe`` but returns Server-Sent Events instead of JSON:
      - ``partial``: transcription has started (empty data)
      - ``final``:   complete transcription text (JSON ``{"text": "...", "chunk_id": "..."}``
      - ``done``:    stream complete
      - ``error``:   error message

    Accepts an optional ``X-Chunk-Id`` header for client-side ordering of
    multiple concurrent chunks.
    """
    config = request.app.state.config

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    chunk_id = request.headers.get("X-Chunk-Id", "0")

    async def _event_generator() -> AsyncGenerator[dict, None]:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue()

        def _run():
            import tempfile
            from pathlib import Path

            stt = _get_stt_engine(config.voice)
            suffix = Path(file.filename or "audio.wav").suffix or ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            try:
                # Signal that transcription has started
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "partial", "data": ""}
                )

                with _voice_lock:
                    text = stt.transcribe_file(tmp_path)

                # Filter hallucinations
                text = _filter_hallucination(text)

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "event": "final",
                        "data": json.dumps({"text": text, "chunk_id": chunk_id}),
                    },
                )
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "done", "data": ""}
                )
            except Exception as e:
                log.exception("Streaming transcription error")
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "error", "data": str(e)}
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        loop.run_in_executor(None, _run)

        while True:
            item = await queue.get()
            yield item
            if item["event"] in ("done", "error"):
                break

    return EventSourceResponse(_event_generator())


# --- Model Management Endpoints ---


@app.get("/api/models/status")
async def models_status(request: Request) -> ModelStatusResponse:
    """Check model setup status and current configuration."""
    config = request.app.state.config

    from giva.hardware import get_hardware_info
    from giva.llm.apple_adapter import check_apple_model_availability
    from giva.models import is_model_setup_complete

    hw = get_hardware_info()

    available, reason = check_apple_model_availability()
    apple_info = AppleModelInfoResponse(available=available, reason=reason)

    return ModelStatusResponse(
        setup_completed=is_model_setup_complete(),
        current_assistant=config.llm.model,
        current_filter=config.llm.filter_model,
        hardware=HardwareInfoResponse(
            chip=hw["chip"],
            ram_gb=hw["ram_gb"],
            gpu_cores=hw["gpu_cores"],
        ),
        apple_model=apple_info,
    )


@app.get("/api/models/available")
async def models_available(request: Request) -> AvailableModelsResponse:
    """Discover compatible models and get LLM recommendations.

    This is an expensive call: queries HuggingFace + runs LLM recommendation.
    """
    config = request.app.state.config

    def _run():
        from giva.hardware import get_hardware_info, max_model_size_gb
        from giva.models import (
            discover_benchmark_keywords,
            filter_compatible_models,
            get_all_cached_model_ids,
            list_mlx_models,
            recommend_models,
            refine_model_search,
        )

        hw = get_hardware_info()
        max_size = max_model_size_gb(hw["ram_gb"])

        # Phase 1: Fetch real benchmark data + LLM analysis for keywords
        # (benchmark fetch is network I/O, no LLM lock needed)
        # Then LLM analyzes the benchmark data
        with _llm_lock:
            keywords = discover_benchmark_keywords(config)

        # Phase 2: Search HuggingFace with benchmark-derived keywords
        all_models = list_mlx_models(
            cache_dir=config.data_dir, extra_keywords=keywords
        )

        # Phase 3: Let LLM review results and suggest more keywords (one round)
        with _llm_lock:
            extra_keywords = refine_model_search(keywords, all_models, config)
        if extra_keywords:
            all_models = list_mlx_models(
                cache_dir=config.data_dir,
                extra_keywords=keywords + extra_keywords,
            )

        compatible = filter_compatible_models(all_models, max_size)

        # Check download status for all cached models
        cached_statuses = get_all_cached_model_ids()

        # Phase 4: Ask LLM to pick from size-appropriate candidates
        with _llm_lock:
            rec = recommend_models(hw, compatible, config)

        return hw, compatible, rec, cached_statuses

    loop = asyncio.get_event_loop()
    hw, compatible, rec, cached_statuses = await loop.run_in_executor(
        None, _run
    )

    from giva.llm.apple_adapter import check_apple_model_availability

    available, reason = check_apple_model_availability()
    apple_info = AppleModelInfoResponse(available=available, reason=reason)

    return AvailableModelsResponse(
        hardware=HardwareInfoResponse(
            chip=hw["chip"],
            ram_gb=hw["ram_gb"],
            gpu_cores=hw["gpu_cores"],
        ),
        compatible_models=[
            ModelInfoResponse(
                model_id=m["model_id"],
                size_gb=m["size_gb"],
                params=m["params"],
                quant=m["quant"],
                downloads=m["downloads"],
                is_downloaded=cached_statuses.get(m["model_id"]) == "complete",
                download_status=cached_statuses.get(
                    m["model_id"], "not_downloaded"
                ),
            )
            for m in compatible
        ],
        recommended=ModelRecommendationResponse(
            assistant=rec["assistant"],
            filter=rec["filter"],
            reasoning=rec["reasoning"],
        ),
        apple_model=apple_info,
    )


@app.post("/api/models/select")
async def models_select(req: ModelSelectRequest, request: Request) -> ModelSelectResponse:
    """Save model choices to user config and resume bootstrap."""
    from giva.models import save_model_choices

    try:
        save_model_choices(req.assistant_model, req.filter_model)

        # Reload config so the server uses the new models
        from giva.config import load_config

        request.app.state.config = load_config()

        # Resume bootstrap if it was waiting for model selection
        bootstrap = getattr(request.app.state, "bootstrap", None)
        if bootstrap and bootstrap.needs_user_input:
            from giva.bootstrap import resume_after_model_selection

            request.app.state.bootstrap_task = asyncio.create_task(
                resume_after_model_selection(request.app)
            )

        return ModelSelectResponse(
            success=True,
            message=f"Models updated: assistant={req.assistant_model}, filter={req.filter_model}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save model choices: {e}")


class ModelCleanupRequest(BaseModel):
    model_id: str = Field(..., min_length=1)


class ModelCleanupResponse(BaseModel):
    success: bool
    freed_mb: float
    message: str


@app.post("/api/models/cleanup")
async def models_cleanup(req: ModelCleanupRequest) -> ModelCleanupResponse:
    """Remove incomplete download temp files for a model.

    Use when a partial download cannot be resumed or should be discarded.
    """
    from giva.models import cleanup_incomplete_download, get_model_download_status

    status = get_model_download_status(req.model_id)
    if status == "not_downloaded":
        return ModelCleanupResponse(
            success=True, freed_mb=0, message="Model not in cache"
        )
    if status == "complete":
        return ModelCleanupResponse(
            success=True, freed_mb=0, message="Model is fully downloaded"
        )

    freed = cleanup_incomplete_download(req.model_id)
    freed_mb = round(freed / (1024 ** 2), 1)
    return ModelCleanupResponse(
        success=True,
        freed_mb=freed_mb,
        message=f"Cleaned up {freed_mb} MB of incomplete downloads",
    )


def _get_repo_size_bytes_safe(model_id: str) -> int:
    """Get model weight file size, returning 0 on any error."""
    try:
        from giva.models import _get_repo_size_bytes
        return _get_repo_size_bytes(model_id)
    except Exception:
        return 0


@app.post("/api/models/download")
async def models_download(req: ModelDownloadRequest, request: Request):
    """Download a model with progress reporting via SSE.

    Polls the HuggingFace cache directory every 2 seconds to report
    real download progress instead of jumping from 0% to 100%.
    """
    import json as _json

    from giva.models import get_model_size_gb, is_model_downloaded

    if is_model_downloaded(req.model_id):
        async def already_done():
            yield {
                "event": "progress",
                "data": '{"percent": 100, "status": "already_downloaded"}',
            }
            yield {"event": "done", "data": ""}
        return EventSourceResponse(already_done())

    loop = asyncio.get_event_loop()
    done_event = asyncio.Event()
    download_error: list[str] = []  # mutable container for error from thread

    total_gb = get_model_size_gb(req.model_id)
    total_bytes = int(total_gb * 1024 * 1024 * 1024) if total_gb else 0

    def _run_download():
        from giva.models import download_model

        try:
            download_model(req.model_id)
        except Exception as e:
            download_error.append(str(e))
        finally:
            loop.call_soon_threadsafe(done_event.set)

    loop.run_in_executor(None, _run_download)

    def _get_cache_size() -> int:
        """Get actual bytes of weight files on disk, including incomplete downloads.

        HuggingFace blobs use content hashes as filenames (not original
        extensions), so we count all large blobs (>10 MB = weight data)
        and all ``.incomplete`` temp files (in-progress downloads).
        """
        try:
            from giva.bootstrap import get_cache_size
            return get_cache_size(req.model_id)
        except Exception:
            return 0

    async def event_generator():
        _total = total_bytes
        total_mb = _total / (1024 ** 2) if _total else 0

        while not done_event.is_set():
            cached = await loop.run_in_executor(None, _get_cache_size)
            dl_mb = round(cached / (1024 ** 2), 1)

            # If we couldn't determine total size, try again from cache growth
            if _total == 0 and cached > 0:
                _total = await loop.run_in_executor(
                    None, lambda: _get_repo_size_bytes_safe(req.model_id)
                )
                total_mb = _total / (1024 ** 2) if _total else 0

            if _total > 0:
                pct = min(round(cached / _total * 100, 1), 99.9)
            else:
                # Indeterminate: UI should show spinner + MB count
                pct = -1
            yield {
                "event": "progress",
                "data": _json.dumps({
                    "percent": pct, "downloaded_mb": dl_mb,
                    "total_mb": round(total_mb, 1),
                }),
            }
            try:
                await asyncio.wait_for(done_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass  # poll again

        if download_error:
            yield {"event": "error", "data": download_error[0]}
        else:
            yield {
                "event": "progress",
                "data": _json.dumps({
                    "percent": 100, "downloaded_mb": round(total_mb, 1),
                    "total_mb": round(total_mb, 1),
                }),
            }
            yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


# --- Bootstrap Endpoints ---


class BootstrapStatusResponse(BaseModel):
    state: str
    ready: bool
    needs_user_input: bool
    progress: dict = {}
    error: Optional[str] = None
    display_message: str


class UpgradeRequest(BaseModel):
    project_root: str = Field(..., min_length=1)


class UpgradeResponse(BaseModel):
    success: bool
    restart_required: bool
    message: str


@app.get("/api/bootstrap/status")
async def bootstrap_status(request: Request) -> BootstrapStatusResponse:
    """Get current bootstrap state."""
    state = request.app.state.bootstrap
    data = state.to_response()
    return BootstrapStatusResponse(**data)


@app.post("/api/bootstrap/start")
async def bootstrap_start(request: Request) -> BootstrapStatusResponse:
    """Trigger bootstrap (if not already running/complete)."""
    from giva.bootstrap import run_bootstrap

    state = request.app.state.bootstrap
    task = request.app.state.bootstrap_task

    if state.is_ready:
        return BootstrapStatusResponse(**state.to_response())

    # If no task running, start one
    if task is None or task.done():
        request.app.state.bootstrap_task = asyncio.create_task(
            run_bootstrap(request.app)
        )

    return BootstrapStatusResponse(**state.to_response())


@app.post("/api/bootstrap/retry")
async def bootstrap_retry(request: Request) -> BootstrapStatusResponse:
    """Retry bootstrap from the last failed step."""
    from giva.bootstrap import run_bootstrap

    state = request.app.state.bootstrap
    task = request.app.state.bootstrap_task

    # Cancel existing task if running
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Clear the error and reset to 'unknown' so all steps are re-attempted
    if state.checkpoint == "failed":
        state.advance("unknown")

    request.app.state.bootstrap_task = asyncio.create_task(
        run_bootstrap(request.app)
    )

    return BootstrapStatusResponse(**state.to_response())


@app.get("/api/bootstrap/stream")
async def bootstrap_stream(request: Request):
    """SSE stream of bootstrap status updates."""
    state = request.app.state.bootstrap
    notifier = request.app.state.bootstrap_notifier

    async def event_generator():
        import json as _json

        # Emit current state immediately
        yield {"event": "status", "data": _json.dumps(state.to_response())}

        # If already ready, emit ready and stop
        if state.is_ready:
            yield {"event": "ready", "data": _json.dumps(state.to_response())}
            return

        # Stream updates
        while True:
            await notifier.wait(timeout=2.0)

            response = state.to_response()
            yield {"event": "status", "data": _json.dumps(response)}

            if state.is_ready:
                yield {"event": "ready", "data": _json.dumps(response)}
                return

            if state.checkpoint == "failed":
                yield {"event": "error", "data": _json.dumps(response)}
                return

            if state.needs_user_input:
                yield {"event": "needs_input", "data": _json.dumps(response)}
                # Don't return — keep streaming so the client sees
                # the transition when the user makes a selection

    return EventSourceResponse(event_generator())


@app.post("/api/upgrade")
async def upgrade(req: UpgradeRequest, request: Request) -> UpgradeResponse:
    """Upgrade giva by re-running pip install from the project root.

    The app should call launchctl bootout + bootstrap after receiving
    ``restart_required: true`` to restart the daemon with updated code.
    """
    import subprocess
    from pathlib import Path

    venv_pip = Path("~/.local/share/giva/.venv/bin/pip").expanduser()
    if not venv_pip.exists():
        raise HTTPException(status_code=500, detail="venv pip not found")

    project_root = Path(req.project_root)
    if not (project_root / "pyproject.toml").exists():
        raise HTTPException(status_code=400, detail="Invalid project root")

    loop = asyncio.get_event_loop()

    def _run_pip():
        result = subprocess.run(
            [str(venv_pip), "install", "-e", ".[voice]"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pip install failed: {result.stderr[-500:]}")

    try:
        await loop.run_in_executor(None, _run_pip)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return UpgradeResponse(
        success=True,
        restart_required=True,
        message="Upgrade successful. Restart the daemon to apply changes.",
    )


# --- Session Helpers ---


async def _run_sync_in_executor(
    store: Store, config,
    session_queues: list | None = None,
) -> tuple[int, int, int]:
    """Run email + calendar sync in a thread pool with progress reporting.

    If *session_queues* is provided, pushes ``sync_progress`` events so the
    UI can show "40/400 emails processed, 12/47 events".
    """
    loop = asyncio.get_event_loop()

    def _broadcast_threadsafe(event: dict):
        """Push an event to all session queues from a background thread.

        Uses ``call_soon_threadsafe`` because asyncio.Queue is NOT thread-safe.
        """
        if not session_queues:
            return
        log.debug(
            "Broadcast %s to %d queues",
            event.get("event", "?"), len(session_queues),
        )
        for q in list(session_queues):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception as exc:
                log.warning("Broadcast failed: %s", exc)

    def _emit_progress(
        mail_synced: int, mail_filtered: int, mail_total: int,
        events_synced: int, events_total: int,
        stage: str,
    ):
        import json as _json
        event = {
            "event": "sync_progress",
            "data": _json.dumps({
                "stage": stage,
                "mail_synced": mail_synced,
                "mail_filtered": mail_filtered,
                "mail_total": mail_total,
                "events_synced": events_synced,
                "events_total": events_total,
            }),
        }
        log.debug("sync_progress: stage=%s mail=%d/%d events=%d",
                  stage, mail_synced, mail_total, events_synced)
        _broadcast_threadsafe(event)

    def _run():
        from giva.intelligence.profile import update_profile
        from giva.sync.calendar import sync_calendar
        from giva.sync.mail import sync_mail_initial

        log.info("Sync executor started (initial sync, %d months)",
                 config.mail.initial_sync_months)

        # --- Email sync with progress callback (date-filtered initial sync) ---
        total_est = 100  # placeholder until JXA reports actual count

        def _on_mail_progress(synced, filtered, total):
            nonlocal total_est
            total_est = total
            _emit_progress(synced, filtered, total_est, 0, 0, "emails")

        _emit_progress(0, 0, total_est, 0, 0, "emails")

        with _llm_lock:
            mail_synced, mail_filtered = sync_mail_initial(
                store, config.mail.mailboxes,
                months=config.mail.initial_sync_months,
                on_progress=_on_mail_progress, config=config,
            )
        log.info("Email sync done: %d synced, %d filtered", mail_synced, mail_filtered)

        # --- Calendar sync ---
        _emit_progress(mail_synced, mail_filtered, mail_synced + mail_filtered, 0, 0, "events")

        events_synced = sync_calendar(
            store, config.calendar.sync_window_past_days,
            config.calendar.sync_window_future_days,
        )
        log.info("Calendar sync done: %d events", events_synced)

        # --- Profile update ---
        _emit_progress(
            mail_synced, mail_filtered, mail_synced + mail_filtered,
            events_synced, events_synced, "profile",
        )

        try:
            update_profile(store, config)
        except Exception as exc:
            log.warning("Profile update error (non-fatal): %s", exc)

        log.info(
            "Sync complete: %d emails, %d filtered, %d events",
            mail_synced, mail_filtered, events_synced,
        )
        return mail_synced, mail_filtered, events_synced

    return await loop.run_in_executor(None, _run)


# --- Session Endpoint (server-driven state machine for the UI) ---
#
# The /api/session endpoint is the UI's single entry point after bootstrap.
# It returns current state + conversation history so the UI can render
# immediately on connect/reconnect.
#
# The /api/session/stream SSE endpoint is a long-lived stream that pushes
# all server-driven events: phase changes, sync results, onboarding questions,
# daily review prompts, periodic sync notifications, etc.
# The UI connects once and stays connected.


@app.get("/api/session")
async def session_state(request: Request) -> SessionStateResponse:
    """Get the current session state for the UI.

    The server's ``checkpoint`` IS the phase — no derivation needed.
    Also returns onboarding conversation history for replay.
    """
    store: Store = request.app.state.store
    bootstrap = request.app.state.bootstrap
    phase = bootstrap.checkpoint

    # Build message history for replay (only relevant during onboarding)
    messages: list[dict] = []
    needs_response = False

    if phase == "onboarding":
        profile = store.get_profile()
        pd = profile.profile_data if profile else {}
        if not pd.get("onboarding_completed", False):
            history = pd.get("onboarding_history", [])
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
            # Waiting for user response if last message is from assistant
            needs_response = (
                pd.get("onboarding_step", 0) > 0
                and (len(messages) == 0 or messages[-1]["role"] == "assistant")
            )

    stats = store.get_stats()

    return SessionStateResponse(
        phase=phase,
        messages=messages,
        needs_response=needs_response,
        stats=stats,
    )


@app.get("/api/session/stream")
async def session_stream(request: Request):
    """Long-lived SSE stream that pushes all server-driven events to the UI.

    This endpoint drives the post-ready lifecycle.  When the UI connects:
      1. Emits current phase
      2. If sync hasn't happened yet → runs initial sync, streaming progress
      3. If onboarding is needed → streams onboarding questions
      4. Marks operational, starts scheduler
      5. Long-lived heartbeat loop, relaying events from background tasks

    Events emitted:
    - phase:<phase>       — phase change (syncing, onboarding, operational)
    - sync_progress       — granular progress: {stage, mail_synced, mail_total, ...}
    - sync_complete       — periodic sync finished, data: {emails, events}
    - onboarding_token    — streamed onboarding question token
    - onboarding_thinking — streamed thinking content
    - onboarding_done     — current onboarding question fully streamed
    - onboarding_complete — onboarding interview finished
    - review_due          — daily review is due
    - stats               — updated stats {emails, events, tasks}
    - heartbeat           — keepalive (every 15s)
    - agent_job_enqueued  — agent job added to queue
    - agent_job_confirmed — pending_confirmation job approved by user
    - agent_job_started   — agent job execution started
    - agent_job_completed — agent job finished successfully
    - agent_job_failed    — agent job failed
    - agent_job_cancelled — agent job cancelled
    """
    store: Store = request.app.state.store
    config = request.app.state.config
    bootstrap = request.app.state.bootstrap

    # Session-scoped event queue for pushing events from background tasks
    session_queue: asyncio.Queue[dict] = asyncio.Queue()

    # Register this session with the app for background task notifications
    if not hasattr(request.app.state, "session_queues"):
        request.app.state.session_queues = []
    request.app.state.session_queues.append(session_queue)

    async def event_generator():
        try:
            # Emit current checkpoint as initial phase
            log.info(
                "Session stream connected: phase=%s (ready=%s, operational=%s)",
                bootstrap.checkpoint, bootstrap.is_ready, bootstrap.is_operational,
            )
            yield {"event": "phase", "data": bootstrap.checkpoint}

            # ----- Post-ready lifecycle (sync → onboarding → operational) -----
            # Only run if models are ready but we haven't reached operational yet.

            if bootstrap.is_ready and not bootstrap.is_operational:
                # Step A: Initial sync (checkpoint moves ready → syncing)
                if not bootstrap.past("syncing"):
                    bootstrap.mark_syncing()
                    yield {"event": "phase", "data": "syncing"}

                    stats = store.get_stats()
                    if stats["emails"] > 0 or stats["events"] > 0:
                        # Data already exists (e.g., manual sync was triggered)
                        log.info("Data exists — skipping initial sync")
                    else:
                        log.info("Starting initial sync...")
                        # Launch sync as a background task so we can yield
                        # progress events from the session queue while it runs.
                        sync_task = asyncio.create_task(
                            _run_sync_in_executor(
                                store, config,
                                session_queues=request.app.state.session_queues,
                            )
                        )
                        # Drain session queue while sync is in progress,
                        # forwarding sync_progress events to the SSE client.
                        while not sync_task.done():
                            try:
                                event = await asyncio.wait_for(
                                    session_queue.get(), timeout=1.0
                                )
                                yield event
                            except asyncio.TimeoutError:
                                continue

                        # Sync finished — drain any remaining queued events
                        while not session_queue.empty():
                            try:
                                yield session_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                        # Get the result (or error) from sync
                        try:
                            mail_s, mail_f, events_s = sync_task.result()
                            log.info(
                                "Sync done: %d emails, %d filtered, %d events",
                                mail_s, mail_f, events_s,
                            )
                            yield {
                                "event": "sync_complete",
                                "data": f'{{"emails": {mail_s}, "events": {events_s}}}',
                            }
                        except Exception as e:
                            log.error("Sync failed: %s", e, exc_info=True)
                            yield {"event": "error", "data": f"Sync failed: {e}"}
                            # Don't block lifecycle — let user retry via Sync button
                else:
                    log.debug("Already past syncing (checkpoint=%s)", bootstrap.checkpoint)

                # Step B: Onboarding (checkpoint moves syncing → onboarding)
                if not bootstrap.past("onboarding"):
                    from giva.intelligence.onboarding import is_onboarding_needed

                    onboarding_needed = is_onboarding_needed(store)
                    if onboarding_needed:
                        bootstrap.mark_onboarding()
                        yield {"event": "phase", "data": "onboarding"}

                        # Check for existing conversation history (resume case)
                        profile = store.get_profile()
                        pd = profile.profile_data if profile else {}
                        history = pd.get("onboarding_history", [])

                        if not history:
                            # Fresh onboarding — stream the first question
                            log.info("Starting fresh onboarding")
                            from giva.intelligence.onboarding import start_onboarding
                            async for event in _sync_gen_to_sse(
                                start_onboarding, store, config
                            ):
                                if event["event"] == "token":
                                    yield {
                                        "event": "onboarding_token",
                                        "data": event["data"],
                                    }
                                elif event["event"] == "thinking":
                                    yield {
                                        "event": "onboarding_thinking",
                                        "data": event["data"],
                                    }
                                elif event["event"] == "done":
                                    yield {"event": "onboarding_done", "data": ""}
                                elif event["event"] == "error":
                                    yield {"event": "error", "data": event["data"]}
                        # else: history exists — UI replays from /api/session.
                        # User responses come via /api/session/respond.
                        # Don't mark operational here — respond endpoint will.
                    else:
                        log.info("Onboarding not needed — skipping to operational")
                else:
                    log.debug(
                        "Already past onboarding (checkpoint=%s)",
                        bootstrap.checkpoint,
                    )

                # Step C: Mark operational if we're past onboarding
                # (either skipped or already completed)
                if bootstrap.checkpoint != "onboarding":
                    # We didn't park at onboarding — go straight to operational
                    bootstrap.mark_operational()
                    _start_scheduler(request.app)
                    log.info("Marked operational, scheduler started")
                    yield {"event": "phase", "data": "operational"}
                else:
                    log.info("Parked at onboarding — waiting for user responses")
            else:
                log.debug(
                    "Skipping lifecycle (ready=%s, operational=%s)",
                    bootstrap.is_ready, bootstrap.is_operational,
                )

            # ----- Long-lived event loop -----
            while True:
                try:
                    event = await asyncio.wait_for(
                        session_queue.get(), timeout=15.0
                    )
                    log.debug("Relaying event: %s", event.get("event", "?"))
                    yield event
                    # When onboarding finishes, the respond endpoint broadcasts
                    # onboarding_complete and advances the checkpoint.
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
                except asyncio.CancelledError:
                    break
        except GeneratorExit:
            log.debug("Session stream: client disconnected")
        except asyncio.CancelledError:
            log.debug("Session stream: task cancelled")
        except Exception as exc:
            log.error("Session stream error: %s", exc, exc_info=True)
            yield {"event": "error", "data": f"Internal error: {exc}"}
        finally:
            log.info("Session stream disconnecting")
            # Unregister session queue
            if hasattr(request.app.state, "session_queues"):
                try:
                    request.app.state.session_queues.remove(session_queue)
                except ValueError:
                    pass

    log.debug("Creating EventSourceResponse for session stream")
    return EventSourceResponse(event_generator())


@app.post("/api/session/respond")
async def session_respond(req: OnboardingRequest, request: Request):
    """Continue onboarding with user's response. Returns SSE stream with next question.

    Tokens are also pushed to all connected /api/session/stream consumers.
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    if not req.response.strip():
        raise HTTPException(status_code=400, detail="Response cannot be empty.")

    from giva.intelligence.onboarding import continue_onboarding

    # Get all connected session streams to broadcast to
    session_queues: list[asyncio.Queue] = getattr(
        request.app.state, "session_queues", []
    )

    def _broadcast(event: dict):
        for q in session_queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def event_generator():
        token_count = 0
        async for event in _sync_gen_to_sse(
            continue_onboarding, req.response, store, config
        ):
            etype = event["event"]
            # Forward to direct SSE consumer
            if etype == "token":
                token_count += 1
                yield {"event": "onboarding_token", "data": event["data"]}
                _broadcast({"event": "onboarding_token", "data": event["data"]})
            elif etype == "thinking":
                yield {"event": "onboarding_thinking", "data": event["data"]}
                _broadcast(
                    {"event": "onboarding_thinking", "data": event["data"]}
                )
            elif etype == "model_loading":
                yield {"event": "model_loading", "data": event["data"]}
                _broadcast({"event": "model_loading", "data": event["data"]})
            elif etype == "done":
                log.info("session/respond: done (%d tokens)", token_count)
                yield {"event": "onboarding_done", "data": ""}
                _broadcast({"event": "onboarding_done", "data": ""})
            elif etype == "error":
                log.error("session/respond: error=%s", event["data"])
                yield {"event": "error", "data": event["data"]}
                _broadcast({"event": "error", "data": event["data"]})

        # Check if onboarding just completed
        profile = store.get_profile()
        pd = profile.profile_data if profile else {}
        is_complete = pd.get("onboarding_completed", False)
        if is_complete:
            from giva.bootstrap import complete_onboarding
            complete_onboarding(request.app)
            yield {"event": "onboarding_complete", "data": "true"}
            _broadcast({"event": "onboarding_complete", "data": "true"})
            _broadcast({"event": "phase", "data": "operational"})

    return EventSourceResponse(event_generator())


# --- Goals Endpoints ---


def _goal_to_response(goal, store, include_detail: bool = False) -> GoalResponse:
    """Convert a Goal model to a GoalResponse, optionally with children/strategies/tasks."""
    progress = store.get_goal_progress(goal.id, limit=5)
    progress_list = [
        GoalProgressResponse(
            id=p.id, goal_id=p.goal_id, note=p.note, source=p.source,
            created_at=p.created_at.isoformat() if p.created_at else None,
        )
        for p in progress
    ]

    children = []
    strategies = []
    tasks = []

    if include_detail:
        for c in store.get_child_goals(goal.id):
            children.append({
                "id": c.id, "title": c.title, "tier": c.tier,
                "status": c.status, "priority": c.priority,
            })
        for s in store.get_strategies(goal.id):
            strategies.append({
                "id": s.id, "strategy_text": s.strategy_text,
                "action_items": s.action_items, "status": s.status,
                "suggested_objectives": s.suggested_objectives or [],
                "created_at": s.created_at.isoformat() if s.created_at else None,
            })
        for t in store.get_tasks_for_goal(goal.id):
            tasks.append({
                "id": t.id, "title": t.title, "priority": t.priority,
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            })

    return GoalResponse(
        id=goal.id, title=goal.title, tier=goal.tier,
        description=goal.description, category=goal.category,
        parent_id=goal.parent_id, status=goal.status, priority=goal.priority,
        target_date=goal.target_date.isoformat() if goal.target_date else None,
        created_at=goal.created_at.isoformat() if goal.created_at else None,
        updated_at=goal.updated_at.isoformat() if goal.updated_at else None,
        progress=progress_list, children=children,
        strategies=strategies, tasks=tasks,
    )


@app.get("/api/goals")
async def get_goals(
    request: Request,
    tier: Optional[str] = Query(None, pattern=r"^(long_term|mid_term|short_term)$"),
    status: str = Query("active", pattern=r"^(active|paused|completed|abandoned)$"),
) -> GoalListResponse:
    """List goals, optionally filtered by tier and status."""
    store: Store = request.app.state.store
    goals = store.get_goals(tier=tier, status=status)
    goal_list = [_goal_to_response(g, store) for g in goals]
    return GoalListResponse(goals=goal_list, count=len(goal_list))


@app.post("/api/goals")
async def create_goal(req: GoalRequest, request: Request) -> GoalResponse:
    """Create a new goal."""
    from datetime import datetime as dt

    from giva.db.models import Goal

    store: Store = request.app.state.store

    target_date = None
    if req.target_date:
        try:
            target_date = dt.fromisoformat(req.target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid target_date format")

    goal = Goal(
        title=req.title, tier=req.tier, description=req.description,
        category=req.category, parent_id=req.parent_id,
        priority=req.priority, target_date=target_date,
    )
    goal_id = store.add_goal(goal)
    created = store.get_goal(goal_id)
    return _goal_to_response(created, store, include_detail=True)


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int, request: Request) -> GoalResponse:
    """Get a goal with children, strategies, tasks, and progress."""
    store: Store = request.app.state.store
    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_response(goal, store, include_detail=True)


@app.put("/api/goals/{goal_id}")
async def update_goal(
    goal_id: int, req: GoalUpdateRequest, request: Request
) -> GoalResponse:
    """Update goal fields."""
    store: Store = request.app.state.store

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    success = store.update_goal(goal_id, **updates)
    if not success:
        raise HTTPException(status_code=404, detail="Goal not found")

    goal = store.get_goal(goal_id)
    return _goal_to_response(goal, store, include_detail=True)


@app.post("/api/goals/{goal_id}/status")
async def update_goal_status_endpoint(
    goal_id: int, req: GoalStatusRequest, request: Request
) -> GoalResponse:
    """Update a goal's status."""
    store: Store = request.app.state.store
    success = store.update_goal_status(goal_id, req.status)
    if not success:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal = store.get_goal(goal_id)
    return _goal_to_response(goal, store)


@app.post("/api/goals/{goal_id}/progress")
async def add_goal_progress(
    goal_id: int, req: GoalProgressRequest, request: Request
) -> GoalProgressResponse:
    """Add a progress note to a goal."""
    store: Store = request.app.state.store
    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    store.add_goal_progress(goal_id, req.note, req.source)
    progress = store.get_goal_progress(goal_id, limit=1)
    p = progress[0]
    return GoalProgressResponse(
        id=p.id, goal_id=p.goal_id, note=p.note, source=p.source,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


@app.get("/api/goals/{goal_id}/progress")
async def get_goal_progress_endpoint(
    goal_id: int,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> list[GoalProgressResponse]:
    """Get progress history for a goal."""
    store: Store = request.app.state.store
    progress = store.get_goal_progress(goal_id, limit=limit)
    return [
        GoalProgressResponse(
            id=p.id, goal_id=p.goal_id, note=p.note, source=p.source,
            created_at=p.created_at.isoformat() if p.created_at else None,
        )
        for p in progress
    ]


@app.post("/api/goals/infer")
async def infer_goals(request: Request):
    """Stream goal inference from profile + recent data via SSE."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.goals import infer_goals as _infer

    async def event_generator():
        import json as _json

        loop = asyncio.get_event_loop()

        def _run():
            with _llm_lock:
                return _infer(store, config)

        goals = await loop.run_in_executor(None, _run)
        yield {"event": "token", "data": _json.dumps({"goals": goals})}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


@app.post("/api/goals/{goal_id}/strategy")
async def generate_strategy(goal_id: int, request: Request):
    """Stream strategy generation for a goal via SSE."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.goals import generate_strategy as _gen_strategy

    async def event_generator():
        async for event in _sync_gen_to_sse(_gen_strategy, goal_id, store, config):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/goals/{goal_id}/strategy/{strategy_id}/accept")
async def accept_strategy(
    goal_id: int, strategy_id: int, request: Request
) -> dict:
    """Accept a strategy (mark as accepted, supersede others)."""
    store: Store = request.app.state.store

    # Supersede existing accepted strategies for this goal
    existing = store.get_strategies(goal_id, status="accepted")
    for s in existing:
        store.update_strategy_status(s.id, "superseded")

    success = store.update_strategy_status(strategy_id, "accepted")
    if not success:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Auto-create mid-term objectives from suggested_objectives
    objectives_created = 0
    strategy = store.get_strategy(strategy_id)
    if strategy and strategy.suggested_objectives:
        from giva.db.models import Goal

        for obj in strategy.suggested_objectives:
            title = obj.get("title", "").strip()
            if not title:
                continue
            child_goal = Goal(
                title=title,
                tier="mid_term",
                description=obj.get("description", ""),
                category=obj.get("category", ""),
                parent_id=goal_id,
            )
            store.add_goal(child_goal)
            objectives_created += 1

        if objectives_created > 0:
            log.info(
                "Created %d mid-term objectives from strategy %d for goal %d",
                objectives_created, strategy_id, goal_id,
            )

    return {
        "success": True,
        "strategy_id": strategy_id,
        "status": "accepted",
        "objectives_created": objectives_created,
    }


@app.post("/api/goals/{goal_id}/plan")
async def generate_plan(goal_id: int, request: Request):
    """Stream tactical plan generation for an objective via SSE."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.goals import generate_tactical_plan

    async def event_generator():
        async for event in _sync_gen_to_sse(
            generate_tactical_plan, goal_id, store, config
        ):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/goals/{goal_id}/plan/accept")
async def accept_plan(goal_id: int, req: PlanAcceptRequest, request: Request) -> dict:
    """Create tasks from a tactical plan JSON."""
    store: Store = request.app.state.store

    from giva.intelligence.goals import accept_plan as _accept

    count = _accept(req.plan_json, goal_id, store)
    return {"success": True, "tasks_created": count}


@app.post("/api/goals/plan/review")
async def review_plans(request: Request):
    """Stream tactical plan review for all active objectives via SSE."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import review_tactical_plans

    async def event_generator():
        async for event in _sync_gen_to_sse(review_tactical_plans, store, config):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/goals/{goal_id}/brainstorm")
async def goal_brainstorm(goal_id: int, request: Request):
    """Plan an AI brainstorm session for a goal via the orchestrator.

    Creates a pending_confirmation job in the agent queue.  The orchestrator
    will research and brainstorm next steps for the goal.
    """
    store: Store = request.app.state.store
    config = request.app.state.config
    aq = getattr(request.app.state, "agent_queue", None)

    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    query = (
        f"Brainstorm next steps and tactics for my goal: {goal.title}\n"
        f"Tier: {goal.tier}, Category: {goal.category}\n"
        f"Description: {goal.description or 'N/A'}\n"
        f"Status: {goal.status}, Priority: {goal.priority}"
    )

    from giva.agents.registry import registry as agent_registry

    orch = agent_registry.get("orchestrator")
    if not orch:
        raise HTTPException(status_code=503, detail="Orchestrator agent not available")

    loop = asyncio.get_event_loop()

    def _plan():
        with _llm_lock:
            return orch.plan_only(query, config)

    plan = await loop.run_in_executor(None, _plan)

    plan_summary = None
    if plan:
        from giva.agents.orchestrator.planner import format_plan_summary

        plan_summary = format_plan_summary(plan)

    from giva.agents.queue import AgentJob, make_job_id

    job = AgentJob(
        job_id=make_job_id(),
        agent_id="orchestrator",
        query=query,
        context={"params": {}, "query": query, "goal_id": goal_id},
        priority=0,
        status="pending_confirmation",
        source="goal",
        goal_id=goal_id,
        plan_summary=plan_summary,
    )
    if aq:
        aq.enqueue(job)

    return {
        "job_id": job.job_id,
        "plan_summary": plan_summary,
        "agent_id": "orchestrator",
    }


@app.post("/api/goals/{goal_id}/chat")
async def goal_chat(goal_id: int, req: GoalChatRequest, request: Request):
    """Goal-scoped chat: sends goal context to LLM. Returns SSE stream.

    Messages are persisted scoped to this goal (not mixed with global chat).
    After the response stream, runs the post-chat agent pipeline to detect
    intents (create_task, create_objective, progress, etc.).
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    from giva.intelligence.queries import handle_query

    # Build goal context prefix — sent to LLM but NOT saved to DB
    goal_context = (
        f"[Context: discussing goal '{goal.title}' ({goal.tier}, {goal.category}). "
        f"Description: {goal.description or 'N/A'}. "
        f"Status: {goal.status}, Priority: {goal.priority}.]"
    )
    original_query = req.query

    async def event_generator():
        async for event in _sync_gen_to_sse(
            handle_query, original_query, store, config,
            goal_id=goal_id, context_prefix=goal_context,
        ):
            yield event
        # Post-chat agents (after stream done, lock released)
        actions = await _run_post_chat(original_query, store, config, goal_id=goal_id)
        if actions:
            yield {
                "event": "agent_actions",
                "data": json.dumps(actions),
            }
        # Agent routing check (same as global chat)
        async for event in _check_agent_routing(
            original_query, store, config, goal_id=goal_id,
        ):
            yield event

    return EventSourceResponse(event_generator())


@app.get("/api/goals/{goal_id}/messages")
async def goal_messages(
    goal_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
):
    """Get persisted chat messages for a goal."""
    store: Store = request.app.state.store
    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    messages = store.get_goal_messages(goal_id, limit=limit)
    return {"messages": messages, "count": len(messages)}


# --- Conversation History Endpoints ---


@app.get("/api/conversations/dates")
async def conversation_dates(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
):
    """Get distinct dates of global chat conversations with preview text."""
    store: Store = request.app.state.store
    dates = store.get_conversation_dates(limit=limit)
    return {"dates": dates, "count": len(dates)}


@app.get("/api/conversations/messages")
async def conversation_messages_by_date(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    limit: int = Query(200, ge=1, le=500),
):
    """Get all global chat messages for a specific date."""
    store: Store = request.app.state.store
    messages = store.get_messages_for_date(date, limit=limit)
    return {"messages": messages, "count": len(messages)}


# --- Daily Review Endpoints ---


@app.get("/api/review/status")
async def review_status(request: Request) -> ReviewStatusResponse:
    """Check if a daily review is due."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import is_review_due

    reviews = store.get_recent_reviews(limit=1)
    last_date = reviews[0].review_date if reviews else None

    return ReviewStatusResponse(
        due=is_review_due(store, config),
        last_review_date=last_date,
    )


@app.post("/api/review/start")
async def review_start(request: Request):
    """Start a daily review. Returns SSE stream with review prompt."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import generate_review

    async def event_generator():
        async for event in _sync_gen_to_sse(generate_review, store, config):
            yield event

    return EventSourceResponse(event_generator())


@app.post("/api/review/respond")
async def review_respond(
    req: ReviewRespondRequest, request: Request
) -> ReviewSummaryResponse:
    """Save user's response to a daily review and extract progress."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import save_review_response

    loop = asyncio.get_event_loop()

    def _run():
        with _llm_lock:
            return save_review_response(req.review_id, req.response, store, config)

    summary = await loop.run_in_executor(None, _run)
    return ReviewSummaryResponse(summary=summary)


@app.get("/api/review/history")
async def review_history(
    request: Request,
    limit: int = Query(7, ge=1, le=30),
) -> list[dict]:
    """Get recent daily reviews."""
    store: Store = request.app.state.store
    reviews = store.get_recent_reviews(limit=limit)
    return [
        {
            "id": r.id,
            "review_date": r.review_date,
            "prompt_text": r.prompt_text,
            "user_response": r.user_response,
            "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reviews
    ]


# --- Weekly Reflection Endpoints ---


@app.get("/api/reflection/status")
async def reflection_status(request: Request) -> dict:
    """Check if a weekly reflection is due."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import is_reflection_due

    return {"due": is_reflection_due(store, config)}


@app.post("/api/reflection/start")
async def reflection_start(request: Request):
    """Start a weekly reflection. Returns SSE stream."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.daily_review import generate_weekly_reflection

    async def event_generator():
        async for event in _sync_gen_to_sse(
            generate_weekly_reflection, store, config
        ):
            yield event

    return EventSourceResponse(event_generator())


# --- Entry point ---


def run():
    """Start the Giva API server."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(
        "giva.server:app",
        host="127.0.0.1",
        port=7483,
        log_level="info",
    )


if __name__ == "__main__":
    run()
