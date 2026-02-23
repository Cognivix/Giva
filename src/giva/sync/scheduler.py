"""Background sync scheduler using threading.Timer.

Broadcasts sync results, review notifications, and other server-driven events
to all connected /api/session/stream consumers via the app's session_queues.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from giva.config import GivaConfig
from giva.db.store import Store
from giva.sync.calendar import sync_calendar
from giva.sync.mail import sync_mail_jxa

log = logging.getLogger(__name__)


class SyncScheduler:
    """Periodic background sync for mail and calendar.

    Broadcasts events to connected UI sessions via ``app.state.session_queues``.
    Acquires ``llm_lock`` before any LLM calls to avoid concurrent access
    to the non-thread-safe MLX ModelManager.

    Optionally runs periodic agent tasks via the AgentQueue (opt-in via config).
    """

    def __init__(
        self,
        store: Store,
        config: GivaConfig,
        app: Any = None,
        llm_lock: Optional[threading.Lock] = None,
        agent_queue: Any = None,
    ):
        self.store = store
        self.config = config
        self.app = app  # FastAPI app instance for session broadcasting
        self._llm_lock = llm_lock
        self._agent_queue = agent_queue
        self._timer: threading.Timer | None = None
        self._strategy_timer: threading.Timer | None = None
        self._agent_timer: threading.Timer | None = None
        self._running = False

    @contextmanager
    def _acquire_llm(self) -> Iterator[None]:
        """Acquire the LLM lock if one was provided."""
        if self._llm_lock is not None:
            with self._llm_lock:
                yield
        else:
            yield

    def _broadcast(self, event: dict) -> None:
        """Push an event to all connected /api/session/stream consumers."""
        if self.app is None:
            return
        queues: list[asyncio.Queue] = getattr(
            self.app.state, "session_queues", []
        )
        loop = getattr(self.app.state, "_event_loop", None)
        if not loop or not queues:
            return
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

    def start(self):
        """Start the background sync loop."""
        self._running = True
        interval = min(
            self.config.mail.sync_interval_minutes,
            self.config.calendar.sync_interval_minutes,
        ) * 60
        self._schedule(interval)

        # Start strategy timer
        strategy_interval = self.config.goals.strategy_interval_hours * 3600
        self._schedule_strategy(strategy_interval)

        # Start agent tasks timer (if enabled and queue available)
        if (
            self._agent_queue
            and self.config.agents.scheduler_agent_enabled
        ):
            agent_interval = self.config.agents.scheduler_agent_interval_minutes * 60
            self._schedule_agent_tasks(agent_interval)
            log.info("Scheduler agent tasks enabled (every %d min)",
                     self.config.agents.scheduler_agent_interval_minutes)

        log.info("Background sync started (every %d minutes)", interval // 60)

    def stop(self):
        """Stop the background sync loop."""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._strategy_timer:
            self._strategy_timer.cancel()
            self._strategy_timer = None
        if self._agent_timer:
            self._agent_timer.cancel()
            self._agent_timer = None
        log.info("Background sync stopped")

    def _schedule(self, interval: float):
        if not self._running:
            return
        self._timer = threading.Timer(interval, self._run_sync, args=(interval,))
        self._timer.daemon = True
        self._timer.start()

    def _schedule_strategy(self, interval: float):
        if not self._running:
            return
        self._strategy_timer = threading.Timer(
            interval, self._run_strategy, args=(interval,)
        )
        self._strategy_timer.daemon = True
        self._strategy_timer.start()

    def _run_sync(self, interval: float):
        """Run a sync cycle, extract tasks, broadcast results, and reschedule."""
        mail_synced = 0
        events_synced = 0

        self._broadcast({"event": "sync_started", "data": ""})

        # Mail sync uses LLM (filter model for email classification)
        try:
            with self._acquire_llm():
                mail_synced, _ = sync_mail_jxa(
                    self.store,
                    self.config.mail.mailboxes,
                    self.config.mail.batch_size,
                    config=self.config,
                )
        except Exception as e:
            log.error("Background mail sync error: %s", e)

        # Calendar sync is pure data — no LLM
        try:
            events_synced = sync_calendar(
                self.store,
                self.config.calendar.sync_window_past_days,
                self.config.calendar.sync_window_future_days,
            )
        except Exception as e:
            log.error("Background calendar sync error: %s", e)

        # Extract tasks from newly synced items (uses assistant model)
        tasks_extracted = 0
        try:
            from giva.intelligence.tasks import extract_tasks

            with self._acquire_llm():
                tasks_extracted = extract_tasks(self.store, self.config)
            if tasks_extracted > 0:
                log.info("Background extraction: %d new tasks", tasks_extracted)
        except Exception as e:
            log.error("Background task extraction error: %s", e)

        # Update user profile from latest email patterns (may use LLM for topics)
        try:
            from giva.intelligence.profile import update_profile

            with self._acquire_llm():
                update_profile(self.store, self.config)
        except Exception as e:
            log.error("Background profile update error: %s", e)

        # Detect goal progress from newly synced data (uses filter model)
        try:
            from giva.intelligence.goals import update_goal_progress_from_sync

            with self._acquire_llm():
                count = update_goal_progress_from_sync(self.store, self.config)
            if count > 0:
                log.info("Background goal progress: %d updates from sync", count)
        except Exception as e:
            log.error("Background goal progress error: %s", e)

        # Broadcast sync completion with stats
        stats = self.store.get_stats()
        self._broadcast({
            "event": "sync_complete",
            "data": _json.dumps({
                "emails_synced": mail_synced,
                "events_synced": events_synced,
                "tasks_extracted": tasks_extracted,
                "total_emails": stats.get("emails", 0),
                "total_events": stats.get("events", 0),
                "pending_tasks": stats.get("pending_tasks", 0),
            }),
        })

        # Broadcast updated stats
        self._broadcast({
            "event": "stats",
            "data": _json.dumps(stats),
        })

        # Check if daily review is due
        try:
            from giva.intelligence.daily_review import is_review_due

            if is_review_due(self.store, self.config):
                self.store.update_sync_state("daily_review", 0, "due")
                self._broadcast({"event": "review_due", "data": "true"})
        except Exception as e:
            log.debug("Review check error: %s", e)

        self._schedule(interval)

    def _run_strategy(self, interval: float):
        """Background job: generate strategies for goals without them."""
        try:
            from giva.intelligence.daily_review import run_background_strategy

            with self._acquire_llm():
                count = run_background_strategy(self.store, self.config)
            if count > 0:
                log.info("Background strategy: generated %d", count)
        except Exception as e:
            log.error("Background strategy error: %s", e)
        finally:
            self._schedule_strategy(interval)

    # ------------------------------------------------------------------
    # Agent tasks (third timer — periodic agent work)
    # ------------------------------------------------------------------

    def _schedule_agent_tasks(self, interval: float):
        if not self._running:
            return
        self._agent_timer = threading.Timer(
            interval, self._run_agent_tasks, args=(interval,)
        )
        self._agent_timer.daemon = True
        self._agent_timer.start()

    def _run_agent_tasks(self, interval: float):
        """Check for automated agent work. Runs periodically when enabled.

        This is infrastructure — specific triggers will be added as agents
        are built.  Currently a no-op placeholder that logs and reschedules.

        Future triggers could include:
        - Stale tasks (no progress in N days) → auto-brainstorm
        - Unactioned important emails → draft response
        - Goal check-ins → progress review
        """
        try:
            log.debug("Scheduler agent tasks: checking for automated work")
            # Placeholder — add specific agent task triggers here as agents
            # are built. Each trigger would create an AgentJob with
            # source="scheduler" and priority=2 (low, behind user work).
        except Exception as e:
            log.error("Scheduler agent tasks error: %s", e)
        finally:
            self._schedule_agent_tasks(interval)
