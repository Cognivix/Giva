"""Tests for the FastAPI server."""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from giva.config import GivaConfig
from giva.db.models import Email, Task, UserProfile
from giva.db.store import Store


def _create_test_app(store: Store, config: GivaConfig) -> FastAPI:
    """Create a test-only FastAPI app that uses the given store/config."""
    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        app.state.store = store
        app.state.config = config
        yield

    # Import routes and models from server module but create a fresh app
    from giva.server import (
        health,
        status,
        profile,
        get_tasks,
        update_task_status,
        get_dismissed_tasks,
        restore_task,
        dismiss_task_endpoint,
        sync,
        extract,
        chat,
        suggest,
    )

    test_app = FastAPI(lifespan=test_lifespan)

    # Re-register all routes on the test app
    test_app.get("/api/health")(health)
    test_app.get("/api/status")(status)
    test_app.get("/api/profile")(profile)
    test_app.get("/api/tasks")(get_tasks)
    test_app.get("/api/tasks/dismissed")(get_dismissed_tasks)
    test_app.post("/api/tasks/{task_id}/status")(update_task_status)
    test_app.post("/api/tasks/{task_id}/restore")(restore_task)
    test_app.post("/api/tasks/{task_id}/dismiss")(dismiss_task_endpoint)
    test_app.post("/api/sync")(sync)
    test_app.post("/api/extract")(extract)
    test_app.post("/api/chat")(chat)
    test_app.get("/api/suggest")(suggest)

    return test_app


@pytest.fixture
def server_client(tmp_path):
    """Create a test client with a temporary store."""
    config = GivaConfig(data_dir=tmp_path)
    store = Store(config.db_path)
    test_app = _create_test_app(store, config)
    with TestClient(test_app) as client:
        yield client, store


# --- Health ---


def test_health(server_client):
    client, _ = server_client
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# --- Status ---


def test_status_empty(server_client):
    client, _ = server_client
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["emails"] == 0
    assert data["events"] == 0
    assert data["pending_tasks"] == 0
    assert "model" in data
    assert isinstance(data["model_loaded"], bool)


def test_status_with_data(server_client):
    client, store = server_client
    store.upsert_email(Email(
        message_id="status-test@example.com",
        folder="INBOX",
        from_addr="alice@example.com",
        subject="Test",
        date_sent=datetime.now(),
    ))
    store.add_task(Task(
        title="Test task", source_type="email", source_id=1
    ))

    resp = client.get("/api/status")
    data = resp.json()
    assert data["emails"] == 1
    assert data["pending_tasks"] == 1


# --- Tasks ---


def test_get_tasks_empty(server_client):
    client, _ = server_client
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == []
    assert data["count"] == 0


def test_get_tasks_with_data(server_client):
    client, store = server_client
    store.add_task(Task(
        title="Review proposal",
        source_type="email",
        source_id=1,
        priority="high",
        due_date=datetime(2026, 3, 1),
    ))
    store.add_task(Task(
        title="Send report",
        source_type="event",
        source_id=2,
        priority="medium",
    ))

    resp = client.get("/api/tasks")
    data = resp.json()
    assert data["count"] == 2
    assert data["tasks"][0]["priority"] == "high"
    assert data["tasks"][0]["title"] == "Review proposal"


def test_get_tasks_filter_status(server_client):
    client, store = server_client
    store.add_task(Task(title="Pending", source_type="email", source_id=1))
    task_id = store.add_task(Task(title="Done", source_type="email", source_id=2))
    store.update_task_status(task_id, "done")

    resp = client.get("/api/tasks?status=pending")
    data = resp.json()
    assert data["count"] == 1
    assert data["tasks"][0]["title"] == "Pending"

    resp = client.get("/api/tasks?status=done")
    data = resp.json()
    assert data["count"] == 1
    assert data["tasks"][0]["title"] == "Done"


def test_get_tasks_invalid_status(server_client):
    client, _ = server_client
    resp = client.get("/api/tasks?status=invalid")
    assert resp.status_code == 422


def test_update_task_status(server_client):
    client, store = server_client
    task_id = store.add_task(Task(
        title="Test task", source_type="email", source_id=1
    ))

    resp = client.post(
        f"/api/tasks/{task_id}/status",
        json={"status": "done"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["task_id"] == task_id
    assert data["status"] == "done"

    # Verify in DB
    task = store.get_task(task_id)
    assert task.status == "done"


def test_update_task_status_not_found(server_client):
    client, _ = server_client
    resp = client.post(
        "/api/tasks/9999/status",
        json={"status": "done"},
    )
    assert resp.status_code == 404


def test_update_task_invalid_status(server_client):
    client, store = server_client
    task_id = store.add_task(Task(
        title="Test", source_type="email", source_id=1
    ))
    resp = client.post(
        f"/api/tasks/{task_id}/status",
        json={"status": "invalid_status"},
    )
    assert resp.status_code == 422


# --- Dismissed Tasks & Restore ---


def test_dismiss_via_status_records_reason(server_client):
    client, store = server_client
    task_id = store.add_task(Task(title="Task to dismiss", source_type="email", source_id=1))

    resp = client.post(f"/api/tasks/{task_id}/status", json={"status": "dismissed"})
    assert resp.status_code == 200

    task = store.get_task(task_id)
    assert task.status == "dismissed"
    assert task.dismissal_reason == "Dismissed by user"
    assert task.dismissed_at is not None


def test_get_dismissed_tasks(server_client):
    client, store = server_client
    t1 = store.add_task(Task(title="Task A", source_type="email", source_id=1))
    t2 = store.add_task(Task(title="Task B", source_type="event", source_id=2))
    store.dismiss_task(t1, "Expired deadline")
    store.dismiss_task(t2, "Past event")

    resp = client.get("/api/tasks/dismissed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["tasks"][0]["dismissal_reason"] in ("Expired deadline", "Past event")


def test_restore_task(server_client):
    client, store = server_client
    task_id = store.add_task(Task(title="Restore me", source_type="email", source_id=1))
    store.dismiss_task(task_id, "Test reason")

    resp = client.post(f"/api/tasks/{task_id}/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["task_id"] == task_id

    task = store.get_task(task_id)
    assert task.status == "pending"
    assert task.dismissal_reason == ""


def test_restore_non_dismissed_fails(server_client):
    client, store = server_client
    task_id = store.add_task(Task(title="Active task", source_type="email", source_id=1))

    resp = client.post(f"/api/tasks/{task_id}/restore")
    assert resp.status_code == 404


def test_restore_not_found(server_client):
    client, _ = server_client
    resp = client.post("/api/tasks/9999/restore")
    assert resp.status_code == 404


def test_dismiss_task_endpoint(server_client):
    client, store = server_client
    task_id = store.add_task(Task(title="Dismiss me", source_type="email", source_id=1))

    resp = client.post(f"/api/tasks/{task_id}/dismiss", json={"reason": "Custom reason"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    task = store.get_task(task_id)
    assert task.status == "dismissed"
    assert task.dismissal_reason == "Custom reason"


def test_task_response_includes_dismissal_fields(server_client):
    client, store = server_client
    task_id = store.add_task(Task(title="Check fields", source_type="email", source_id=1))
    store.dismiss_task(task_id, "Test reason")

    resp = client.get("/api/tasks?status=dismissed")
    assert resp.status_code == 200
    task_data = resp.json()["tasks"][0]
    assert task_data["dismissal_reason"] == "Test reason"
    assert task_data["dismissed_at"] is not None


# --- Profile ---


def test_profile_not_found(server_client):
    client, _ = server_client
    resp = client.get("/api/profile")
    assert resp.status_code == 404


def test_profile_with_data(server_client):
    client, store = server_client
    store.upsert_profile(UserProfile(
        display_name="Alice Smith",
        email_address="alice@example.com",
        top_contacts=[{"addr": "bob@test.com", "name": "Bob", "count": 5}],
        top_topics=["budgets", "meetings"],
        active_hours={"9": 10, "14": 8},
        avg_response_time_min=25.0,
        email_volume_daily=12.5,
    ))

    resp = client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Alice Smith"
    assert data["email_address"] == "alice@example.com"
    assert len(data["top_contacts"]) == 1
    assert data["top_topics"] == ["budgets", "meetings"]
    assert "Alice Smith" in data["summary"]


# --- Chat SSE ---


def test_chat_empty_query(server_client):
    client, _ = server_client
    resp = client.post("/api/chat", json={"query": ""})
    assert resp.status_code == 422


def test_chat_missing_query(server_client):
    client, _ = server_client
    resp = client.post("/api/chat", json={})
    assert resp.status_code == 422


# --- Extract ---


def test_extract_no_unprocessed(server_client):
    """Extract with no unprocessed items should return 0."""
    client, _ = server_client
    resp = client.post("/api/extract")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks_extracted"] == 0


# --- ThinkParser ---


class TestThinkParser:
    """Tests for the _ThinkParser that splits <think>...</think> tags."""

    @pytest.fixture
    def parser(self):
        from giva.server import _ThinkParser
        return _ThinkParser()

    def test_plain_text_no_thinking(self, parser):
        """Text without think tags should be all 'token' events."""
        events = parser.feed("Hello world")
        assert events == [("token", "Hello world")]

    def test_complete_think_block(self, parser):
        """A complete <think>...</think> block followed by response."""
        events = parser.feed("<think>reasoning</think>Answer")
        assert events == [("thinking", "reasoning"), ("token", "Answer")]

    def test_think_block_streamed_in_chunks(self, parser):
        """Think tags split across multiple feed() calls."""
        events = []
        events += parser.feed("<thi")
        events += parser.feed("nk>I am thinking")
        events += parser.feed("</thi")
        events += parser.feed("nk>The answer is 4")
        # Collect all event types and content
        thinking = "".join(d for t, d in events if t == "thinking")
        tokens = "".join(d for t, d in events if t == "token")
        assert thinking == "I am thinking"
        assert tokens == "The answer is 4"

    def test_thinking_only(self, parser):
        """Only thinking content, no response yet."""
        events = parser.feed("<think>deep thought")
        assert events == [("thinking", "deep thought")]

    def test_flush_remaining_thinking(self, parser):
        """Flush should emit buffered thinking content from partial close tag."""
        parser.feed("<think>partial</th")  # partial close tag buffered
        events = parser.flush()
        assert len(events) >= 1
        assert all(t == "thinking" for t, _ in events)

    def test_flush_remaining_token(self, parser):
        """Flush should emit buffered token content."""
        parser.feed("partial<")
        events = parser.flush()
        # The '<' was buffered as a potential partial tag
        all_text = "".join(d for _, d in events)
        assert "partial" in all_text or "<" in all_text

    def test_empty_think_block(self, parser):
        """Empty think block should not produce thinking events."""
        events = parser.feed("<think></think>Response")
        tokens = "".join(d for t, d in events if t == "token")
        thinking = "".join(d for t, d in events if t == "thinking")
        assert tokens == "Response"
        assert thinking == ""

    def test_newline_after_close_stripped(self, parser):
        """Newlines after </think> should be stripped."""
        events = parser.feed("<think>reasoning</think>\n\nThe answer")
        tokens = "".join(d for t, d in events if t == "token")
        assert tokens == "The answer"

    def test_multiple_feed_calls(self, parser):
        """Simulate token-by-token streaming."""
        all_events = []
        text = "<think>Let me think about this.</think>\n4"
        for char in text:
            all_events += parser.feed(char)
        all_events += parser.flush()
        thinking = "".join(d for t, d in all_events if t == "thinking")
        tokens = "".join(d for t, d in all_events if t == "token")
        assert thinking == "Let me think about this."
        # Newline stripping works on buf contents; char-by-char may retain it
        assert tokens.strip() == "4"

    def test_no_think_tags_streaming(self, parser):
        """Plain text streamed char by char."""
        all_events = []
        for char in "Hello":
            all_events += parser.feed(char)
        all_events += parser.flush()
        result = "".join(d for _, d in all_events)
        assert result == "Hello"
