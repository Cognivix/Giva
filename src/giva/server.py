"""FastAPI server exposing Giva's intelligence layer over REST + SSE.

Provides a clean API for the SwiftUI menu bar app (or any HTTP client).
Streaming endpoints use Server-Sent Events (SSE) for real-time token delivery.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from giva import __version__
from giva.config import load_config
from giva.db.store import Store

log = logging.getLogger(__name__)

# Lock to serialize all LLM calls (MLX ModelManager is not thread-safe)
_llm_lock = threading.Lock()


# --- Pydantic Request/Response Models ---


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)


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


class ExtractResponse(BaseModel):
    tasks_extracted: int


class HealthResponse(BaseModel):
    status: str
    version: str


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


async def _sync_gen_to_sse(gen_fn, *args, **kwargs) -> AsyncGenerator[dict, None]:
    """Bridge a synchronous Generator[str, None, None] to async SSE events.

    Runs the generator in a thread pool (with LLM lock) and pushes tokens
    into an asyncio.Queue for non-blocking consumption.
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _run():
        with _llm_lock:
            try:
                for token in gen_fn(*args, **kwargs):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"event": "token", "data": token}
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


# --- Routes ---


@app.get("/api/health")
async def health() -> HealthResponse:
    """Health check — lightweight, no DB or model access."""
    return HealthResponse(status="ok", version=__version__)


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

    return SyncResponse(
        mail_synced=mail_synced,
        mail_filtered=mail_filtered,
        events_synced=events_synced,
        profile_updated=profile_updated,
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
    """Streaming chat via SSE. Returns token-by-token LLM response."""
    store: Store = request.app.state.store
    config = request.app.state.config

    from giva.intelligence.queries import handle_query

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
