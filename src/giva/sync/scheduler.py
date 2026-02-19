"""Background sync scheduler using threading.Timer."""

from __future__ import annotations

import logging
import threading

from giva.config import GivaConfig
from giva.db.store import Store
from giva.sync.calendar import sync_calendar
from giva.sync.mail import sync_mail_jxa

log = logging.getLogger(__name__)


class SyncScheduler:
    """Periodic background sync for mail and calendar."""

    def __init__(self, store: Store, config: GivaConfig):
        self.store = store
        self.config = config
        self._timer: threading.Timer | None = None
        self._running = False

    def start(self):
        """Start the background sync loop."""
        self._running = True
        interval = min(
            self.config.mail.sync_interval_minutes,
            self.config.calendar.sync_interval_minutes,
        ) * 60
        self._schedule(interval)
        log.info("Background sync started (every %d minutes)", interval // 60)

    def stop(self):
        """Stop the background sync loop."""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        log.info("Background sync stopped")

    def _schedule(self, interval: float):
        if not self._running:
            return
        self._timer = threading.Timer(interval, self._run_sync, args=(interval,))
        self._timer.daemon = True
        self._timer.start()

    def _run_sync(self, interval: float):
        """Run a sync cycle, extract tasks, and reschedule."""
        try:
            sync_mail_jxa(
                self.store,
                self.config.mail.mailboxes,
                self.config.mail.batch_size,
                config=self.config,
            )
            sync_calendar(
                self.store,
                self.config.calendar.sync_window_past_days,
                self.config.calendar.sync_window_future_days,
            )
        except Exception as e:
            log.error("Background sync error: %s", e)

        # Extract tasks from newly synced items
        try:
            from giva.intelligence.tasks import extract_tasks

            count = extract_tasks(self.store, self.config)
            if count > 0:
                log.info("Background extraction: %d new tasks", count)
        except Exception as e:
            log.error("Background task extraction error: %s", e)

        # Update user profile from latest email patterns
        try:
            from giva.intelligence.profile import update_profile

            update_profile(self.store, self.config)
        except Exception as e:
            log.error("Background profile update error: %s", e)
        finally:
            self._schedule(interval)
