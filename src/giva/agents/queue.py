"""Agent execution queue: thread-safe priority queue for background agent jobs.

All agent entry points (chat routing, task AI, goal brainstorm, scheduler)
enqueue jobs here.  A single consumer thread processes them sequentially,
acquiring ``_llm_lock`` per job and voluntarily yielding to active user chat.

SSE events are broadcast to connected clients via the same session_queues
mechanism used by the SyncScheduler.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from giva.agents.registry import registry
from giva.agents.router import execute_agent
from giva.config import GivaConfig
from giva.db.store import Store

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentJob — a single unit of work
# ---------------------------------------------------------------------------


@dataclass
class AgentJob:
    """A single agent execution request."""

    job_id: str
    agent_id: str
    query: str
    context: dict = field(default_factory=dict)
    priority: int = 0  # lower = higher priority (0=user, 2=scheduler)
    status: str = "pending"
    # pending | pending_confirmation | running | completed | failed | cancelled
    source: str = "chat"  # chat | task | goal | scheduler
    goal_id: Optional[int] = None
    task_id: Optional[int] = None
    plan_summary: Optional[str] = None
    result: Optional[dict] = None  # serialized AgentResult fields
    error: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialize for REST / SSE payloads."""
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "query": self.query[:200],
            "priority": self.priority,
            "status": self.status,
            "source": self.source,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "plan_summary": self.plan_summary,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


def make_job_id() -> str:
    """Generate a unique job ID."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# AgentQueue
# ---------------------------------------------------------------------------


class AgentQueue:
    """Thread-safe priority queue for agent execution jobs.

    Design:
    - Single consumer thread processes jobs sequentially.
    - ``_chat_active`` event is set while user chat is streaming — the
      consumer waits for it to clear before starting the next job.
    - ``_llm_lock`` is acquired per job (not held across the whole queue).
    - SSE events are broadcast to all ``app.state.session_queues`` via
      the same pattern the SyncScheduler uses.

    Parameters
    ----------
    store:
        SQLite store for logging agent executions.
    config:
        Application config.
    llm_lock:
        ``threading.Lock`` serializing all LLM calls.
    broadcast_fn:
        Callable that pushes an SSE event dict to all session queues.
        Signature: ``broadcast_fn(event_dict) -> None``.
    """

    def __init__(
        self,
        store: Store,
        config: GivaConfig,
        llm_lock: threading.Lock,
        broadcast_fn: Callable[[dict], None],
    ):
        self.store = store
        self.config = config
        self._llm_lock = llm_lock
        self._broadcast = broadcast_fn

        # PriorityQueue items are (priority, created_at, job_id)
        self._queue: queue.PriorityQueue[tuple[int, float, str]] = queue.PriorityQueue()
        self._jobs: dict[str, AgentJob] = {}
        self._jobs_lock = threading.Lock()  # protects _jobs dict
        self._consumer_thread: Optional[threading.Thread] = None
        self._running = False

        # Set while user chat is actively streaming.
        # The consumer thread checks this and waits.
        self.chat_active = threading.Event()

        # Recent completed jobs (ring buffer)
        self._max_history = 50

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the consumer thread."""
        if self._running:
            return
        self._running = True
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            name="agent-queue-consumer",
            daemon=True,
        )
        self._consumer_thread.start()
        log.info("AgentQueue started")

    def stop(self) -> None:
        """Stop the consumer thread gracefully."""
        self._running = False
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5)
        log.info("AgentQueue stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, job: AgentJob) -> str:
        """Add a job to the queue.

        Jobs with ``status="pending_confirmation"`` are stored but NOT
        pushed to the priority queue — they wait for :meth:`confirm`.
        """
        with self._jobs_lock:
            self._jobs[job.job_id] = job

        if job.status == "pending_confirmation":
            log.info(
                "Job %s enqueued (pending confirmation, agent=%s, source=%s)",
                job.job_id[:8], job.agent_id, job.source,
            )
        else:
            self._queue.put((job.priority, job.created_at, job.job_id))
            log.info(
                "Job %s enqueued (priority=%d, agent=%s, source=%s)",
                job.job_id[:8], job.priority, job.agent_id, job.source,
            )

        self._broadcast_event("agent_job_enqueued", job)
        return job.job_id

    def confirm(self, job_id: str) -> bool:
        """Confirm a pending_confirmation job and push it to the queue."""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if not job or job.status != "pending_confirmation":
            return False

        job.status = "pending"
        self._queue.put((job.priority, job.created_at, job.job_id))
        log.info("Job %s confirmed, queued for execution", job.job_id[:8])
        self._broadcast_event("agent_job_confirmed", job)
        return True

    def cancel(self, job_id: str) -> bool:
        """Cancel a pending or pending_confirmation job.

        Running jobs cannot be cancelled (they hold _llm_lock).
        """
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if not job or job.status not in ("pending", "pending_confirmation"):
            return False

        job.status = "cancelled"
        job.completed_at = time.monotonic()
        log.info("Job %s cancelled", job.job_id[:8])
        self._broadcast_event("agent_job_cancelled", job)
        return True

    def get_job(self, job_id: str) -> Optional[AgentJob]:
        """Get a job by ID."""
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> list[AgentJob]:
        """List jobs, optionally filtered by status.

        Returns most recent first.
        """
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    @property
    def active_count(self) -> int:
        """Number of running + pending jobs."""
        with self._jobs_lock:
            return sum(
                1 for j in self._jobs.values()
                if j.status in ("pending", "running")
            )

    # ------------------------------------------------------------------
    # Consumer thread
    # ------------------------------------------------------------------

    def _consumer_loop(self) -> None:
        """Main consumer loop. Runs in a daemon thread."""
        while self._running:
            try:
                _, _, job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # Look up the job — it might have been cancelled
            with self._jobs_lock:
                job = self._jobs.get(job_id)
            if not job or job.status == "cancelled":
                continue

            # Wait for active chat to finish before starting a new job
            while self.chat_active.is_set() and self._running:
                time.sleep(0.5)

            if not self._running:
                break

            self._execute_job(job)

            # Prune old completed jobs from memory
            self._prune_history()

    def _execute_job(self, job: AgentJob) -> None:
        """Execute a single agent job under _llm_lock."""
        job.status = "running"
        self._broadcast_event("agent_job_started", job)
        log.info("Executing job %s (agent=%s)", job.job_id[:8], job.agent_id)

        start = time.monotonic()
        try:
            # MCP agents (model_tier="none") don't need the LLM lock
            agent = registry.get(job.agent_id)
            if agent and agent.manifest.model_tier == "none":
                result = execute_agent(
                    job.agent_id, job.query, job.context,
                    self.store, self.config,
                )
            else:
                with self._llm_lock:
                    result = execute_agent(
                        job.agent_id, job.query, job.context,
                        self.store, self.config,
                    )

            duration_ms = int((time.monotonic() - start) * 1000)

            job.status = "completed" if result.success else "failed"
            job.completed_at = time.monotonic()
            job.result = {
                "success": result.success,
                "output": result.output,
                "actions": result.actions,
                "artifacts": result.artifacts,
                "error": result.error,
            }
            job.error = result.error

            # Log to DB
            self.store.log_agent_execution(
                job.agent_id, job.query, job.context.get("params", {}),
                result.success, result.output[:500], result.artifacts,
                result.error or "", duration_ms,
            )

            log.info(
                "Job %s %s in %dms (agent=%s)",
                job.job_id[:8], job.status, duration_ms, job.agent_id,
            )

        except Exception as e:
            job.status = "failed"
            job.completed_at = time.monotonic()
            job.error = str(e)
            log.error("Job %s failed: %s", job.job_id[:8], e)

        event_name = (
            "agent_job_completed" if job.status == "completed"
            else "agent_job_failed"
        )
        self._broadcast_event(event_name, job)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _broadcast_event(self, event_name: str, job: AgentJob) -> None:
        """Push an SSE event about a job to all session consumers."""
        try:
            self._broadcast({
                "event": event_name,
                "data": json.dumps(job.to_dict()),
            })
        except Exception as e:
            log.debug("Failed to broadcast %s: %s", event_name, e)

    def _prune_history(self) -> None:
        """Remove oldest completed/failed/cancelled jobs beyond _max_history."""
        with self._jobs_lock:
            terminal = [
                j for j in self._jobs.values()
                if j.status in ("completed", "failed", "cancelled")
            ]
        if len(terminal) <= self._max_history:
            return
        terminal.sort(key=lambda j: j.completed_at or j.created_at)
        to_remove = terminal[: len(terminal) - self._max_history]
        with self._jobs_lock:
            for j in to_remove:
                self._jobs.pop(j.job_id, None)
