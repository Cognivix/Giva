"""Tests for the background sync scheduler."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from giva.config import GivaConfig
from giva.sync.scheduler import SyncScheduler


@pytest.fixture
def scheduler(tmp_db, config):
    """Create a scheduler with a fake app and lock."""
    lock = threading.Lock()
    sched = SyncScheduler(
        store=tmp_db, config=config, app=None, llm_lock=lock,
    )
    yield sched
    sched.stop()


class TestSchedulerLifecycle:

    def test_start_and_stop(self, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        sched.start()
        assert sched._running is True
        assert sched._timer is not None

        sched.stop()
        assert sched._running is False
        assert sched._timer is None

    def test_stop_without_start(self, tmp_db, config):
        sched = SyncScheduler(store=tmp_db, config=config)
        sched.stop()  # Should not raise
        assert sched._running is False

    def test_start_creates_strategy_timer(self, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        sched.start()
        assert sched._strategy_timer is not None
        sched.stop()

    def test_agent_timer_disabled_by_default(self, tmp_db, config):
        sched = SyncScheduler(store=tmp_db, config=config)
        sched.start()
        assert sched._agent_timer is None
        sched.stop()


class TestAcquireLlm:

    def test_with_lock(self, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        with sched._acquire_llm():
            assert lock.locked()
        assert not lock.locked()

    def test_without_lock(self, tmp_db, config):
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=None)
        with sched._acquire_llm():
            pass  # Should not raise


class TestBroadcast:

    def test_broadcasts_to_queues(self, tmp_db, config):
        import asyncio

        loop = asyncio.new_event_loop()
        queue = asyncio.Queue()

        app = MagicMock()
        app.state.session_queues = [queue]
        app.state._event_loop = loop

        sched = SyncScheduler(store=tmp_db, config=config, app=app)
        sched._broadcast({"event": "test", "data": "hello"})

        # Run pending callbacks
        loop.run_until_complete(asyncio.sleep(0))
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "test"
        loop.close()

    def test_broadcast_no_app(self, tmp_db, config):
        sched = SyncScheduler(store=tmp_db, config=config, app=None)
        sched._broadcast({"event": "test"})  # Should not raise


class TestRunSync:

    @patch("giva.sync.scheduler.sync_calendar", return_value=5)
    @patch("giva.sync.scheduler.sync_mail_jxa", return_value=(10, 3))
    def test_runs_sync_cycle(self, mock_mail, mock_cal, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        sched._running = True

        # Prevent rescheduling
        sched._schedule = MagicMock()

        sched._run_sync(900)

        mock_mail.assert_called_once()
        mock_cal.assert_called_once()
        sched._schedule.assert_called_once_with(900)

    @patch("giva.sync.scheduler.sync_calendar", side_effect=RuntimeError("fail"))
    @patch("giva.sync.scheduler.sync_mail_jxa", return_value=(0, 0))
    def test_calendar_error_doesnt_crash(self, mock_mail, mock_cal, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        sched._running = True
        sched._schedule = MagicMock()

        sched._run_sync(900)  # Should not raise
        sched._schedule.assert_called_once()

    @patch("giva.sync.scheduler.sync_calendar", return_value=0)
    @patch("giva.sync.scheduler.sync_mail_jxa", side_effect=RuntimeError("fail"))
    def test_mail_error_doesnt_crash(self, mock_mail, mock_cal, tmp_db, config):
        lock = threading.Lock()
        sched = SyncScheduler(store=tmp_db, config=config, llm_lock=lock)
        sched._running = True
        sched._schedule = MagicMock()

        sched._run_sync(900)  # Should not raise
        sched._schedule.assert_called_once()


class TestDeepenSync:

    def test_skips_when_at_max_depth(self, tmp_db, config):
        """No deepening when already at max months."""
        sched = SyncScheduler(store=tmp_db, config=config)

        # Set all mailboxes at max depth
        for mbox in config.mail.mailboxes:
            tmp_db.update_sync_state(
                f"mail_depth:{mbox}", 1000, f"deep_{config.mail.deep_sync_max_months}mo",
            )

        sched._maybe_deepen_sync()  # Should be a no-op

    def test_skips_when_recently_deepened(self, tmp_db, config):
        """No deepening within 24h of last attempt."""
        from datetime import datetime

        sched = SyncScheduler(store=tmp_db, config=config)

        tmp_db.update_sync_state("deep_sync", 100, "deep_4mo")

        sched._maybe_deepen_sync()  # Should be a no-op (too soon)
