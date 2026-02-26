"""Tests for the SQLite store."""

from datetime import datetime, timedelta

from giva.db.models import Email, Event


def test_upsert_and_retrieve_email(tmp_db):
    email = Email(
        message_id="test-123@example.com",
        folder="INBOX",
        from_addr="alice@example.com",
        from_name="Alice",
        subject="Hello World",
        date_sent=datetime.now(),
        body_plain="This is a test email.",
        to_addrs=["bob@example.com"],
    )
    row_id = tmp_db.upsert_email(email)
    assert row_id > 0

    recent = tmp_db.get_recent_emails(limit=1)
    assert len(recent) == 1
    assert recent[0].subject == "Hello World"
    assert recent[0].from_addr == "alice@example.com"


def test_email_fts_search(tmp_db):
    email = Email(
        message_id="fts-test@example.com",
        folder="INBOX",
        from_addr="bob@example.com",
        from_name="Bob Smith",
        subject="Quarterly budget review",
        date_sent=datetime.now(),
        body_plain="Please review the Q3 budget spreadsheet attached.",
    )
    tmp_db.upsert_email(email)

    results = tmp_db.search_emails("budget")
    assert len(results) == 1
    assert results[0].subject == "Quarterly budget review"

    results = tmp_db.search_emails("spreadsheet")
    assert len(results) == 1


def test_email_count(tmp_db):
    assert tmp_db.email_count() == 0
    email = Email(
        message_id="count-test@example.com",
        folder="INBOX",
        from_addr="x@y.com",
        subject="Test",
        date_sent=datetime.now(),
    )
    tmp_db.upsert_email(email)
    assert tmp_db.email_count() == 1


def test_upsert_email_dedup(tmp_db):
    email = Email(
        message_id="dedup@example.com",
        folder="INBOX",
        from_addr="x@y.com",
        subject="Original",
        date_sent=datetime.now(),
        is_read=False,
    )
    tmp_db.upsert_email(email)
    assert tmp_db.email_count() == 1

    # Upsert same message_id — should update, not duplicate
    email.is_read = True
    tmp_db.upsert_email(email)
    assert tmp_db.email_count() == 1

    recent = tmp_db.get_recent_emails(limit=1)
    assert recent[0].is_read is True


def test_upsert_and_retrieve_event(tmp_db):
    event = Event(
        uid="evt-123",
        calendar_name="Work",
        summary="Team standup",
        dtstart=datetime.now() + timedelta(hours=1),
        dtend=datetime.now() + timedelta(hours=2),
        location="Zoom",
        attendees=[{"name": "Alice", "status": "accepted"}],
    )
    row_id = tmp_db.upsert_event(event)
    assert row_id > 0

    upcoming = tmp_db.get_upcoming_events(days=1)
    assert len(upcoming) == 1
    assert upcoming[0].summary == "Team standup"


def test_get_emails_from(tmp_db):
    for i, sender in enumerate(["alice@co.com", "bob@co.com", "alice@co.com"]):
        tmp_db.upsert_email(Email(
            message_id=f"from-test-{i}@co.com",
            folder="INBOX",
            from_addr=sender,
            from_name=sender.split("@")[0].title(),
            subject=f"Message {i}",
            date_sent=datetime.now(),
        ))

    results = tmp_db.get_emails_from("alice")
    assert len(results) == 2


def test_sync_state(tmp_db):
    assert tmp_db.get_sync_state("mail:INBOX") is None

    tmp_db.update_sync_state("mail:INBOX", 42, "success")
    state = tmp_db.get_sync_state("mail:INBOX")
    assert state is not None
    assert state["last_count"] == 42
    assert state["last_status"] == "success"


def test_conversations(tmp_db):
    tmp_db.add_message("user", "Hello")
    tmp_db.add_message("assistant", "Hi there!")

    messages = tmp_db.get_recent_messages(limit=10)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_task_scoped_conversations(tmp_db):
    """Task-scoped messages are isolated from global and goal-scoped messages."""
    from giva.db.models import Task

    # Create a task
    task = Task(title="Review budget", source_type="manual", source_id=0)
    task_id = tmp_db.add_task(task)
    assert task_id > 0

    # Add global, goal-scoped (fake goal_id=999), and task-scoped messages
    tmp_db.add_message("user", "global message")
    tmp_db.add_message("user", "task message 1", task_id=task_id)
    tmp_db.add_message("assistant", "task reply 1", task_id=task_id)

    # Global should only see global
    global_msgs = tmp_db.get_recent_messages(limit=10)
    assert len(global_msgs) == 1
    assert global_msgs[0]["content"] == "global message"

    # Task-scoped should see only task messages
    task_msgs = tmp_db.get_task_messages(task_id)
    assert len(task_msgs) == 2
    assert task_msgs[0]["content"] == "task message 1"
    assert task_msgs[1]["content"] == "task reply 1"

    # get_recent_messages with task_id should match get_task_messages
    recent_task = tmp_db.get_recent_messages(limit=10, task_id=task_id)
    assert len(recent_task) == 2


def test_stats(tmp_db):
    stats = tmp_db.get_stats()
    assert stats["emails"] == 0
    assert stats["events"] == 0
    assert stats["pending_tasks"] == 0


# --- Dismiss & Restore ---


def test_dismiss_task_records_reason_and_timestamp(tmp_db):
    from giva.db.models import Task

    task_id = tmp_db.add_task(
        Task(title="Reply to Alice", source_type="email", source_id=1)
    )
    assert tmp_db.dismiss_task(task_id, "Deadline has passed")

    task = tmp_db.get_task(task_id)
    assert task.status == "dismissed"
    assert task.dismissal_reason == "Deadline has passed"
    assert task.dismissed_at is not None


def test_dismiss_task_not_found(tmp_db):
    assert tmp_db.dismiss_task(9999, "no such task") is False


def test_restore_task_returns_to_pending(tmp_db):
    from giva.db.models import Task

    task_id = tmp_db.add_task(
        Task(title="Book flight", source_type="manual", source_id=0)
    )
    tmp_db.dismiss_task(task_id, "Dismissed by user")
    assert tmp_db.restore_task(task_id)

    task = tmp_db.get_task(task_id)
    assert task.status == "pending"
    assert task.dismissal_reason == ""
    assert task.dismissed_at is None


def test_restore_non_dismissed_task_is_noop(tmp_db):
    from giva.db.models import Task

    task_id = tmp_db.add_task(
        Task(title="Active task", source_type="email", source_id=1)
    )
    # Task is pending — restore should fail
    assert tmp_db.restore_task(task_id) is False


def test_get_dismissed_tasks_ordered_by_dismissed_at(tmp_db):
    from giva.db.models import Task
    import time

    t1 = tmp_db.add_task(
        Task(title="First", source_type="email", source_id=1)
    )
    tmp_db.dismiss_task(t1, "Expired")
    # Sleep >1s to ensure SQLite datetime('now') differs
    time.sleep(1.1)

    t2 = tmp_db.add_task(
        Task(title="Second", source_type="email", source_id=2)
    )
    tmp_db.dismiss_task(t2, "Answered")

    dismissed = tmp_db.get_dismissed_tasks()
    assert len(dismissed) == 2
    # Newest first
    assert dismissed[0].title == "Second"
    assert dismissed[1].title == "First"


def test_dismissed_task_has_reason_in_results(tmp_db):
    from giva.db.models import Task

    task_id = tmp_db.add_task(
        Task(title="Review doc", source_type="event", source_id=5)
    )
    tmp_db.dismiss_task(task_id, "Event already happened")

    dismissed = tmp_db.get_dismissed_tasks()
    assert len(dismissed) == 1
    assert dismissed[0].dismissal_reason == "Event already happened"
