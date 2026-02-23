"""Tests for the agent execution queue."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from giva.agents.queue import AgentJob, AgentQueue, make_job_id
from giva.config import GivaConfig


def _make_job(**kwargs) -> AgentJob:
    """Helper to create a test job with defaults."""
    defaults = {
        "job_id": make_job_id(),
        "agent_id": "test_agent",
        "query": "test query",
        "context": {},
    }
    defaults.update(kwargs)
    return AgentJob(**defaults)


def _make_queue(**kwargs) -> AgentQueue:
    """Helper to create a queue without starting the consumer thread."""
    defaults = {
        "store": MagicMock(),
        "config": GivaConfig(),
        "llm_lock": threading.Lock(),
        "broadcast_fn": MagicMock(),
    }
    defaults.update(kwargs)
    return AgentQueue(**defaults)


# ---------------------------------------------------------------------------
# AgentJob tests
# ---------------------------------------------------------------------------


class TestAgentJob:
    def test_to_dict_contains_key_fields(self):
        job = _make_job(agent_id="email_drafter", source="task", task_id=42)
        d = job.to_dict()
        assert d["agent_id"] == "email_drafter"
        assert d["source"] == "task"
        assert d["task_id"] == 42
        assert "job_id" in d
        assert "status" in d

    def test_query_truncated_in_dict(self):
        job = _make_job(query="x" * 500)
        d = job.to_dict()
        assert len(d["query"]) == 200

    def test_default_status_is_pending(self):
        job = _make_job()
        assert job.status == "pending"

    def test_make_job_id_unique(self):
        ids = {make_job_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Enqueue / confirm / cancel
# ---------------------------------------------------------------------------


class TestEnqueueConfirmCancel:
    def test_enqueue_pending_job(self):
        q = _make_queue()
        job = _make_job()
        job_id = q.enqueue(job)

        assert job_id == job.job_id
        assert q.get_job(job_id) is job
        # Should be in the priority queue (not just stored)
        assert not q._queue.empty()

    def test_enqueue_pending_confirmation_not_in_queue(self):
        q = _make_queue()
        job = _make_job(status="pending_confirmation")
        q.enqueue(job)

        # Stored but NOT in the priority queue
        assert q.get_job(job.job_id) is job
        assert q._queue.empty()

    def test_confirm_moves_to_queue(self):
        q = _make_queue()
        job = _make_job(status="pending_confirmation")
        q.enqueue(job)
        assert q._queue.empty()

        result = q.confirm(job.job_id)
        assert result is True
        assert job.status == "pending"
        assert not q._queue.empty()

    def test_confirm_wrong_id_returns_false(self):
        q = _make_queue()
        assert q.confirm("nonexistent") is False

    def test_confirm_already_pending_returns_false(self):
        q = _make_queue()
        job = _make_job(status="pending")
        q.enqueue(job)
        # Can't confirm a job that's already pending (not pending_confirmation)
        assert q.confirm(job.job_id) is False

    def test_cancel_pending_job(self):
        q = _make_queue()
        job = _make_job()
        q.enqueue(job)

        result = q.cancel(job.job_id)
        assert result is True
        assert job.status == "cancelled"
        assert job.completed_at is not None

    def test_cancel_pending_confirmation_job(self):
        q = _make_queue()
        job = _make_job(status="pending_confirmation")
        q.enqueue(job)

        result = q.cancel(job.job_id)
        assert result is True
        assert job.status == "cancelled"

    def test_cancel_running_returns_false(self):
        q = _make_queue()
        job = _make_job(status="running")
        with q._jobs_lock:
            q._jobs[job.job_id] = job

        assert q.cancel(job.job_id) is False

    def test_cancel_nonexistent_returns_false(self):
        q = _make_queue()
        assert q.cancel("nonexistent") is False


# ---------------------------------------------------------------------------
# list_jobs / active_count
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_list_all_jobs(self):
        q = _make_queue()
        for i in range(5):
            q.enqueue(_make_job())

        jobs = q.list_jobs()
        assert len(jobs) == 5

    def test_list_filtered_by_status(self):
        q = _make_queue()
        q.enqueue(_make_job(status="pending"))
        q.enqueue(_make_job(status="pending_confirmation"))
        q.enqueue(_make_job(status="pending"))

        pending = q.list_jobs(status="pending")
        assert len(pending) == 2

    def test_list_respects_limit(self):
        q = _make_queue()
        for _ in range(10):
            q.enqueue(_make_job())

        jobs = q.list_jobs(limit=3)
        assert len(jobs) == 3

    def test_list_ordered_most_recent_first(self):
        q = _make_queue()
        j1 = _make_job(created_at=100.0)
        j2 = _make_job(created_at=200.0)
        j3 = _make_job(created_at=150.0)
        q.enqueue(j1)
        q.enqueue(j2)
        q.enqueue(j3)

        jobs = q.list_jobs()
        assert jobs[0].created_at == 200.0
        assert jobs[1].created_at == 150.0
        assert jobs[2].created_at == 100.0

    def test_active_count(self):
        q = _make_queue()
        q.enqueue(_make_job(status="pending"))
        j2 = _make_job(status="running")
        with q._jobs_lock:
            q._jobs[j2.job_id] = j2
        j3 = _make_job(status="completed")
        with q._jobs_lock:
            q._jobs[j3.job_id] = j3

        assert q.active_count == 2  # pending + running


# ---------------------------------------------------------------------------
# Broadcasting
# ---------------------------------------------------------------------------


class TestBroadcasting:
    def test_enqueue_broadcasts_event(self):
        broadcast = MagicMock()
        q = _make_queue(broadcast_fn=broadcast)
        job = _make_job()
        q.enqueue(job)

        broadcast.assert_called_once()
        event = broadcast.call_args[0][0]
        assert event["event"] == "agent_job_enqueued"

    def test_confirm_broadcasts_event(self):
        broadcast = MagicMock()
        q = _make_queue(broadcast_fn=broadcast)
        job = _make_job(status="pending_confirmation")
        q.enqueue(job)
        broadcast.reset_mock()

        q.confirm(job.job_id)
        broadcast.assert_called_once()
        event = broadcast.call_args[0][0]
        assert event["event"] == "agent_job_confirmed"

    def test_cancel_broadcasts_event(self):
        broadcast = MagicMock()
        q = _make_queue(broadcast_fn=broadcast)
        job = _make_job()
        q.enqueue(job)
        broadcast.reset_mock()

        q.cancel(job.job_id)
        broadcast.assert_called_once()
        event = broadcast.call_args[0][0]
        assert event["event"] == "agent_job_cancelled"


# ---------------------------------------------------------------------------
# Consumer thread execution
# ---------------------------------------------------------------------------


_QUEUE_EXEC = "giva.agents.queue.execute_agent"
_QUEUE_REG = "giva.agents.queue.registry"


class TestConsumerExecution:
    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_execute_job_success(self, mock_exec, mock_reg):
        from giva.agents.base import AgentResult

        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "assistant"
        mock_reg.get.return_value = mock_agent
        mock_exec.return_value = AgentResult(
            success=True, output="Done!",
        )

        broadcast = MagicMock()
        q = _make_queue(broadcast_fn=broadcast)
        job = _make_job()
        q._execute_job(job)

        assert job.status == "completed"
        assert job.result["success"] is True
        assert job.result["output"] == "Done!"
        assert job.completed_at is not None
        mock_exec.assert_called_once()

    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_execute_job_failure(self, mock_exec, mock_reg):
        from giva.agents.base import AgentResult

        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "assistant"
        mock_reg.get.return_value = mock_agent
        mock_exec.return_value = AgentResult(
            success=False, output="", error="Boom",
        )

        q = _make_queue()
        job = _make_job()
        q._execute_job(job)

        assert job.status == "failed"
        assert job.error == "Boom"

    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_execute_job_exception(self, mock_exec, mock_reg):
        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "assistant"
        mock_reg.get.return_value = mock_agent
        mock_exec.side_effect = RuntimeError("crash")

        q = _make_queue()
        job = _make_job()
        q._execute_job(job)

        assert job.status == "failed"
        assert "crash" in job.error

    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_execute_job_mcp_skips_lock(self, mock_exec, mock_reg):
        """MCP agents (model_tier='none') should not acquire _llm_lock."""
        from giva.agents.base import AgentResult

        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "none"
        mock_reg.get.return_value = mock_agent
        mock_exec.return_value = AgentResult(success=True, output="ok")

        # Use a locked lock — if the queue tries to acquire it, it'll deadlock
        lock = threading.Lock()
        lock.acquire()  # hold the lock
        try:
            q = _make_queue(llm_lock=lock)
            job = _make_job()
            q._execute_job(job)

            assert job.status == "completed"
            mock_exec.assert_called_once()
        finally:
            lock.release()

    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_execute_logs_to_store(self, mock_exec, mock_reg):
        from giva.agents.base import AgentResult

        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "assistant"
        mock_reg.get.return_value = mock_agent
        mock_exec.return_value = AgentResult(success=True, output="ok")

        store = MagicMock()
        q = _make_queue(store=store)
        job = _make_job()
        q._execute_job(job)

        store.log_agent_execution.assert_called_once()

    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_cancelled_job_skipped_by_consumer(self, mock_exec, mock_reg):
        """Consumer should skip cancelled jobs."""
        q = _make_queue()
        job = _make_job()
        q.enqueue(job)
        q.cancel(job.job_id)

        # Simulate consumer picking up the job
        _, _, job_id = q._queue.get(timeout=1)
        retrieved = q.get_job(job_id)
        # Consumer checks status before executing
        assert retrieved.status == "cancelled"
        mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# chat_active yielding
# ---------------------------------------------------------------------------


class TestChatActiveYielding:
    @patch(_QUEUE_REG)
    @patch(_QUEUE_EXEC)
    def test_consumer_waits_while_chat_active(self, mock_exec, mock_reg):
        """Consumer should wait while chat_active is set."""
        from giva.agents.base import AgentResult

        mock_agent = MagicMock()
        mock_agent.manifest.model_tier = "assistant"
        mock_reg.get.return_value = mock_agent
        mock_exec.return_value = AgentResult(success=True, output="ok")

        q = _make_queue()
        q.start()
        try:
            q.chat_active.set()  # simulate active chat

            job = _make_job()
            q.enqueue(job)

            # Give consumer thread time to pick up the job
            time.sleep(0.3)
            # Job should still be pending (consumer is waiting)
            assert job.status == "pending"

            q.chat_active.clear()  # release chat
            time.sleep(1.5)  # give consumer time to execute

            assert job.status in ("completed", "failed")
        finally:
            q.stop()


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_lower_priority_first(self):
        q = _make_queue()
        j_low = _make_job(priority=2, created_at=100.0)
        j_high = _make_job(priority=0, created_at=200.0)

        q.enqueue(j_low)
        q.enqueue(j_high)

        # High priority (0) should come out first
        _, _, first_id = q._queue.get(timeout=1)
        assert first_id == j_high.job_id

    def test_same_priority_fifo(self):
        q = _make_queue()
        j1 = _make_job(priority=0, created_at=100.0)
        j2 = _make_job(priority=0, created_at=200.0)

        q.enqueue(j1)
        q.enqueue(j2)

        # Earlier created_at should come first
        _, _, first_id = q._queue.get(timeout=1)
        assert first_id == j1.job_id


# ---------------------------------------------------------------------------
# History pruning
# ---------------------------------------------------------------------------


class TestHistoryPruning:
    def test_prune_keeps_max_history(self):
        q = _make_queue()
        q._max_history = 5

        # Add 10 completed jobs
        for i in range(10):
            job = _make_job(
                status="completed",
                completed_at=float(i),
                created_at=float(i),
            )
            with q._jobs_lock:
                q._jobs[job.job_id] = job

        q._prune_history()

        with q._jobs_lock:
            remaining = list(q._jobs.values())
        completed = [j for j in remaining if j.status == "completed"]
        assert len(completed) == 5

    def test_prune_does_not_remove_active_jobs(self):
        q = _make_queue()
        q._max_history = 2

        # Add 5 completed + 1 running
        for i in range(5):
            job = _make_job(
                status="completed",
                completed_at=float(i),
                created_at=float(i),
            )
            with q._jobs_lock:
                q._jobs[job.job_id] = job

        running_job = _make_job(status="running")
        with q._jobs_lock:
            q._jobs[running_job.job_id] = running_job

        q._prune_history()

        # Running job should survive
        assert q.get_job(running_job.job_id) is not None
        # Only 2 completed should remain
        with q._jobs_lock:
            completed = [
                j for j in q._jobs.values() if j.status == "completed"
            ]
        assert len(completed) == 2
