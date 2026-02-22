"""FastAPI server exposing Giva's intelligence layer over REST + SSE.

Provides a clean API for the SwiftUI menu bar app (or any HTTP client).
Streaming endpoints use Server-Sent Events (SSE) for real-time token delivery.
Voice endpoints provide TTS audio chunks in SSE and STT transcription.
"""

from __future__ import annotations

import asyncio
import base64
import io
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
    created_at: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    count: int


class UpdateStatusResponse(BaseModel):
    success: bool
    task_id: int
    status: str


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


class HealthResponse(BaseModel):
    status: str
    version: str
    commit: str


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


class ModelRecommendationResponse(BaseModel):
    assistant: str
    filter: str
    reasoning: str


class ModelStatusResponse(BaseModel):
    setup_completed: bool
    current_assistant: str
    current_filter: str
    hardware: HardwareInfoResponse


class AvailableModelsResponse(BaseModel):
    hardware: HardwareInfoResponse
    compatible_models: list[ModelInfoResponse]
    recommended: ModelRecommendationResponse


class ModelSelectRequest(BaseModel):
    assistant_model: str = Field(..., min_length=1)
    filter_model: str = Field(..., min_length=1)


class ModelSelectResponse(BaseModel):
    success: bool
    message: str


class ModelDownloadRequest(BaseModel):
    model_id: str = Field(..., min_length=1)


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize config and store on startup."""
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(config.db_path)
    app.state.store = store
    app.state.config = config
    log.info("Giva server started — DB: %s", config.db_path)
    yield
    log.info("Giva server shutting down")


# --- App ---

app = FastAPI(
    title="Giva API",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _run():
        try:
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
    return HealthResponse(status="ok", version=__version__, commit=_GIT_COMMIT)


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


@app.get("/api/tasks")
async def get_tasks(
    request: Request,
    status: Optional[str] = Query(None, pattern=r"^(pending|in_progress|done|dismissed)$"),
    limit: int = Query(50, ge=1, le=200),
) -> TaskListResponse:
    """List tasks, optionally filtered by status."""
    store: Store = request.app.state.store
    tasks = store.get_tasks(status=status, limit=limit)

    task_list = [
        TaskResponse(
            id=t.id,
            title=t.title,
            description=t.description,
            source_type=t.source_type,
            source_id=t.source_id,
            priority=t.priority,
            due_date=t.due_date.isoformat() if t.due_date else None,
            status=t.status,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )
        for t in tasks
    ]

    return TaskListResponse(tasks=task_list, count=len(task_list))


@app.post("/api/tasks/{task_id}/status")
async def update_task_status(
    task_id: int,
    req: UpdateStatusRequest,
    request: Request,
) -> UpdateStatusResponse:
    """Update a task's status (done, dismissed, etc.)."""
    store: Store = request.app.state.store

    success = store.update_task_status(task_id, req.status)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    return UpdateStatusResponse(success=True, task_id=task_id, status=req.status)


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


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    """Streaming chat via SSE. Returns token-by-token LLM response.

    When voice=true, also emits "audio_chunk" events containing base64-encoded
    WAV audio for each sentence (synthesized via Qwen3-TTS).
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.queries import handle_query

    if req.voice and config.voice.enabled:
        async def event_generator():
            async for event in _sync_gen_to_sse_with_voice(
                handle_query, config, req.query, store, config
            ):
                yield event
    else:
        async def event_generator():
            async for event in _sync_gen_to_sse(handle_query, req.query, store, config):
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
    """Full tabula rasa: clear DB, caches, and user config.

    After this, the app should re-trigger model setup.
    Downloaded HuggingFace models are preserved (expensive to re-download).
    """
    store: Store = request.app.state.store
    config = request.app.state.config

    # 1. Clear all DB data
    store.reset_all_data()

    # 2. Delete model and benchmark caches
    from pathlib import Path

    cache_files = [
        config.data_dir / "model_cache.json",
        config.data_dir / "benchmark_cache.json",
    ]
    for cache_file in cache_files:
        try:
            cache_file.unlink(missing_ok=True)
        except Exception:
            pass

    # 3. Delete user config (forces model setup on next launch)
    user_config = Path("~/.config/giva/config.toml").expanduser()
    try:
        user_config.unlink(missing_ok=True)
    except Exception:
        pass

    # 4. Reload config (will fall back to defaults since user config is gone)
    from giva.config import load_config

    request.app.state.config = load_config()

    return ResetResponse(
        success=True,
        message="All data, caches, and settings cleared. Please set up your models again.",
    )


@app.post("/api/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)) -> TranscribeResponse:
    """Transcribe an audio file (WAV/MP3) to text using Whisper MLX.

    Accepts multipart file upload. Returns transcribed text.
    """
    config = request.app.state.config
    if not config.voice.enabled:
        raise HTTPException(status_code=400, detail="Voice mode is not enabled in config.")

    # Read uploaded audio
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    def _run_transcribe():
        import tempfile
        from pathlib import Path

        from giva.audio.stt import STTEngine

        stt = STTEngine(config.voice)
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

    return TranscribeResponse(text=text)


# --- Model Management Endpoints ---


@app.get("/api/models/status")
async def models_status(request: Request) -> ModelStatusResponse:
    """Check model setup status and current configuration."""
    config = request.app.state.config

    from giva.hardware import get_hardware_info
    from giva.models import is_model_setup_complete

    hw = get_hardware_info()

    return ModelStatusResponse(
        setup_completed=is_model_setup_complete(),
        current_assistant=config.llm.model,
        current_filter=config.llm.filter_model,
        hardware=HardwareInfoResponse(
            chip=hw["chip"],
            ram_gb=hw["ram_gb"],
            gpu_cores=hw["gpu_cores"],
        ),
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
            get_downloaded_model_ids,
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

        # Check which models are already downloaded
        downloaded_ids = get_downloaded_model_ids()

        # Phase 4: Ask LLM to pick from size-appropriate candidates
        with _llm_lock:
            rec = recommend_models(hw, compatible, config)

        return hw, compatible, rec, downloaded_ids

    loop = asyncio.get_event_loop()
    hw, compatible, rec, downloaded_ids = await loop.run_in_executor(None, _run)

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
                is_downloaded=m["model_id"] in downloaded_ids,
            )
            for m in compatible
        ],
        recommended=ModelRecommendationResponse(
            assistant=rec["assistant"],
            filter=rec["filter"],
            reasoning=rec["reasoning"],
        ),
    )


@app.post("/api/models/select")
async def models_select(req: ModelSelectRequest, request: Request) -> ModelSelectResponse:
    """Save model choices to user config."""
    from giva.models import save_model_choices

    try:
        save_model_choices(req.assistant_model, req.filter_model)

        # Reload config so the server uses the new models
        from giva.config import load_config

        request.app.state.config = load_config()

        return ModelSelectResponse(
            success=True,
            message=f"Models updated: assistant={req.assistant_model}, filter={req.filter_model}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save model choices: {e}")


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
        """Get actual bytes on disk for this model, including incomplete downloads.

        ``scan_cache_dir()`` only counts *committed* blobs, which stays at 0
        until each multi-GB shard finishes.  We measure the real directory
        size instead so the progress bar moves during the download.
        """
        try:
            from pathlib import Path

            cache_root = Path.home() / ".cache" / "huggingface" / "hub"
            # HF Hub stores repos as models--<org>--<name>
            dir_name = "models--" + req.model_id.replace("/", "--")
            model_dir = cache_root / dir_name
            if not model_dir.is_dir():
                return 0
            return sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
        except Exception:
            return 0

    async def event_generator():
        total_mb = total_bytes / (1024 ** 2) if total_bytes else 0

        while not done_event.is_set():
            cached = await loop.run_in_executor(None, _get_cache_size)
            if total_bytes > 0:
                pct = min(round(cached / total_bytes * 100, 1), 99.9)
                dl_mb = round(cached / (1024 ** 2), 1)
            else:
                pct = 0
                dl_mb = round(cached / (1024 ** 2), 1)
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
