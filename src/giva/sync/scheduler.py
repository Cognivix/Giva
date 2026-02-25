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

    # ------------------------------------------------------------------
    # Power-aware scheduling helpers
    # ------------------------------------------------------------------

    def _should_skip_sync(self) -> str | None:
        """Return a reason string if sync should be skipped, else None."""
        if not self.config.power.enabled:
            return None
        try:
            from giva.utils.power import get_power_state

            state = get_power_state()
            pwr = self.config.power

            if state.thermal_state >= pwr.thermal_pause_threshold:
                return (
                    f"thermal state {state.thermal_state} "
                    f"(threshold {pwr.thermal_pause_threshold})"
                )
            if (
                state.on_battery
                and state.battery_percent is not None
                and state.battery_percent < pwr.battery_pause_threshold
            ):
                return (
                    f"battery at {state.battery_percent}% "
                    f"(threshold {pwr.battery_pause_threshold}%)"
                )
        except Exception as e:
            log.debug("Power state check failed: %s", e)
        return None

    def _should_skip_heavy(self) -> str | None:
        """Return a reason string if heavy work should be skipped, else None."""
        if not self.config.power.enabled:
            return None
        try:
            from giva.utils.power import get_power_state

            state = get_power_state()
            pwr = self.config.power

            if state.thermal_state >= pwr.thermal_defer_heavy_threshold:
                return (
                    f"thermal state {state.thermal_state} "
                    f"(heavy threshold {pwr.thermal_defer_heavy_threshold})"
                )
            if (
                state.on_battery
                and state.battery_percent is not None
                and state.battery_percent < pwr.battery_defer_heavy_threshold
            ):
                return (
                    f"battery at {state.battery_percent}% "
                    f"(heavy threshold {pwr.battery_defer_heavy_threshold}%)"
                )
        except Exception as e:
            log.debug("Power state check failed: %s", e)
        return None

    def _set_background_qos(self) -> None:
        """Set the current thread to background QoS priority."""
        if not self.config.power.enabled:
            return
        try:
            from giva.utils.power import set_thread_qos_background
            set_thread_qos_background()
        except Exception:
            pass

    def _maybe_unload_idle_models(self) -> None:
        """Unload LLM models that have been idle longer than the configured timeout."""
        if not self.config.power.enabled:
            return
        try:
            from giva.llm.engine import manager

            timeout = self.config.power.model_idle_timeout_minutes * 60
            with self._acquire_llm():
                unloaded = manager.unload_idle(timeout)
            if unloaded:
                log.info("Unloaded idle models: %s", ", ".join(unloaded))
        except Exception as e:
            log.debug("Idle model unload failed: %s", e)

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
        self._set_background_qos()

        # Power-aware: skip entire sync cycle if conditions are bad
        skip_reason = self._should_skip_sync()
        if skip_reason:
            log.info("Skipping sync cycle: %s", skip_reason)
            self._maybe_unload_idle_models()
            self._schedule(interval)
            return

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

        # Incremental deep sync (extend email history over time)
        try:
            self._maybe_deepen_sync()
        except Exception as e:
            log.error("Deep sync check error: %s", e)

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

        # Check if weekly reflection is due
        try:
            from giva.intelligence.daily_review import is_reflection_due

            if is_reflection_due(self.store, self.config):
                self._broadcast({"event": "reflection_due", "data": "true"})
        except Exception as e:
            log.debug("Reflection check error: %s", e)

        # Unload models that have been idle past the timeout
        self._maybe_unload_idle_models()

        self._schedule(interval)

    def _run_strategy(self, interval: float):
        """Background job: generate strategies for goals without them."""
        self._set_background_qos()

        skip_reason = self._should_skip_heavy()
        if skip_reason:
            log.info("Skipping strategy generation: %s", skip_reason)
            self._schedule_strategy(interval)
            return

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
    # Incremental deep sync — extend email history post-bootstrap
    # ------------------------------------------------------------------

    # Deepening tiers (months): bootstrap → incremental expansion
    _DEEPENING_TIERS = [4, 8, 12, 18, 24]

    def _maybe_deepen_sync(self) -> None:
        """Incrementally extend email history if conditions are met.

        Guards:
        - Power state allows heavy work
        - ≥24h since last deepening attempt
        - Hasn't reached ``deep_sync_max_months`` yet
        - Only one tier advancement per invocation

        Runs ``sync_mail_initial`` at the next tier, then re-profiles.
        """
        skip_reason = self._should_skip_heavy()
        if skip_reason:
            log.info("Skipping deep sync: %s", skip_reason)
            return

        from datetime import datetime, timedelta

        import json as _json

        from giva.sync.mail import sync_mail_initial

        max_months = self.config.mail.deep_sync_max_months

        # Check last deepening timestamp
        state = self.store.get_sync_state("deep_sync")
        if state and state.get("last_sync"):
            try:
                last_sync = datetime.fromisoformat(state["last_sync"])
                if datetime.now() - last_sync < timedelta(hours=24):
                    return  # Too soon
            except (ValueError, TypeError):
                pass

        # Determine current depth (worst-case across mailboxes)
        current_months = 0
        for mailbox in self.config.mail.mailboxes:
            mbox_state = self.store.get_sync_state(f"mail_depth:{mailbox}")
            if mbox_state and mbox_state.get("last_status"):
                status = mbox_state["last_status"]
                # Parse "initial_Xmo" or "deep_Xmo" format
                for prefix in ("initial_", "deep_"):
                    if status.startswith(prefix) and status.endswith("mo"):
                        try:
                            depth = int(status[len(prefix):-2])
                            current_months = max(current_months, depth)
                        except ValueError:
                            pass

        if current_months >= max_months:
            log.debug("Deep sync: already at max depth (%d months)", max_months)
            return

        # Find next tier
        next_months = None
        for tier in self._DEEPENING_TIERS:
            if tier > current_months and tier <= max_months:
                next_months = tier
                break

        if next_months is None:
            return

        log.info(
            "Deep sync: extending from %d to %d months",
            current_months, next_months,
        )

        try:
            with self._acquire_llm():
                synced, filtered = sync_mail_initial(
                    self.store, self.config.mail.mailboxes,
                    months=next_months, config=self.config,
                )

            # Update depth state for each mailbox
            for mailbox in self.config.mail.mailboxes:
                self.store.update_sync_state(
                    f"mail_depth:{mailbox}", synced, f"deep_{next_months}mo"
                )

            self.store.update_sync_state("deep_sync", synced, f"deep_{next_months}mo")

            # Re-profile after deepening (includes writing style re-analysis)
            try:
                from giva.intelligence.profile import update_profile

                with self._acquire_llm():
                    update_profile(self.store, self.config)
            except Exception as e:
                log.warning("Post-deep-sync profile update failed: %s", e)

            log.info(
                "Deep sync complete: %d synced, %d filtered, now at %d months",
                synced, filtered, next_months,
            )

            self._broadcast({
                "event": "deep_sync_complete",
                "data": _json.dumps({
                    "months": next_months,
                    "synced": synced,
                    "filtered": filtered,
                }),
            })

        except Exception as e:
            log.error("Deep sync to %d months failed: %s", next_months, e)

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

        Current triggers:
        - Weekly goal inference: re-analyze profile + data to suggest new goals
        """
        self._set_background_qos()

        skip_reason = self._should_skip_heavy()
        if skip_reason:
            log.info("Skipping agent tasks: %s", skip_reason)
            self._schedule_agent_tasks(interval)
            return

        try:
            log.debug("Scheduler agent tasks: checking for automated work")

            # Weekly goal inference — only runs when reflection is due
            # (same schedule: target day + hour), preventing redundant runs.
            try:
                from giva.intelligence.daily_review import is_reflection_due

                if is_reflection_due(self.store, self.config):
                    from giva.intelligence.goals import infer_goals

                    with self._acquire_llm():
                        result = infer_goals(self.store, self.config)
                    if result:
                        log.info(
                            "Scheduler: weekly goal inference returned %d suggestions",
                            len(result.goals) if hasattr(result, "goals") else 0,
                        )
            except Exception as e:
                log.debug("Scheduler goal inference error: %s", e)

        except Exception as e:
            log.error("Scheduler agent tasks error: %s", e)
        finally:
            self._schedule_agent_tasks(interval)
