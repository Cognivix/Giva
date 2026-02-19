"""Tests for task extraction pipeline."""

from datetime import datetime

from giva.db.models import Email, Event, Task
from giva.intelligence.tasks import _parse_extraction_response, _parse_due_date


# --- Unit tests for JSON parsing (no LLM needed) ---


def test_parse_extraction_clean_json():
    response = '{"tasks": [{"title": "Review budget", "priority": "high", "due_date": "2026-03-01", "source_quote": "Please review by March 1st"}], "has_actionable_items": true}'
    result = _parse_extraction_response(response)
    assert result.has_actionable_items is True
    assert len(result.tasks) == 1
    assert result.tasks[0].title == "Review budget"
    assert result.tasks[0].priority.value == "high"
    assert result.tasks[0].due_date == "2026-03-01"
    assert result.tasks[0].source_quote == "Please review by March 1st"


def test_parse_extraction_markdown_fenced():
    response = """Here are the tasks:
```json
{"tasks": [{"title": "Send report", "priority": "medium"}], "has_actionable_items": true}
```
"""
    result = _parse_extraction_response(response)
    assert len(result.tasks) == 1
    assert result.tasks[0].title == "Send report"


def test_parse_extraction_no_tasks():
    response = '{"tasks": [], "has_actionable_items": false}'
    result = _parse_extraction_response(response)
    assert result.has_actionable_items is False
    assert len(result.tasks) == 0


def test_parse_extraction_garbage():
    result = _parse_extraction_response("I couldn't find any tasks here.")
    assert len(result.tasks) == 0
    assert result.has_actionable_items is False


def test_parse_extraction_with_extra_text():
    response = """Based on the email, here are the tasks:
{"tasks": [{"title": "Follow up with Bob"}], "has_actionable_items": true}
Hope that helps!"""
    result = _parse_extraction_response(response)
    assert len(result.tasks) == 1
    assert result.tasks[0].title == "Follow up with Bob"


def test_parse_extraction_partial_validation():
    """Should salvage valid tasks even if some fail validation."""
    response = '{"tasks": [{"title": "Valid task", "priority": "low"}, {"bad_field": "no title"}], "has_actionable_items": true}'
    result = _parse_extraction_response(response)
    # Should salvage at least the valid task
    assert len(result.tasks) >= 1
    assert result.tasks[0].title == "Valid task"


def test_parse_extraction_multiple_tasks():
    response = '{"tasks": [{"title": "Task 1", "priority": "high"}, {"title": "Task 2", "priority": "low", "due_date": "2026-04-15"}], "has_actionable_items": true}'
    result = _parse_extraction_response(response)
    assert len(result.tasks) == 2
    assert result.tasks[0].priority.value == "high"
    assert result.tasks[1].due_date == "2026-04-15"


# --- Due date parsing ---


def test_parse_due_date_valid():
    assert _parse_due_date("2026-03-01") == datetime(2026, 3, 1)


def test_parse_due_date_with_time():
    result = _parse_due_date("2026-03-01T14:30:00")
    assert result is not None
    assert result.year == 2026
    assert result.month == 3
    assert result.day == 1


def test_parse_due_date_none():
    assert _parse_due_date(None) is None


def test_parse_due_date_empty():
    assert _parse_due_date("") is None


def test_parse_due_date_invalid():
    assert _parse_due_date("not a date") is None


# --- Store integration tests ---


def test_add_and_get_tasks(tmp_db):
    task = Task(
        title="Review Q3 budget",
        source_type="email",
        source_id=1,
        priority="high",
        due_date=datetime(2026, 3, 1),
    )
    task_id = tmp_db.add_task(task)
    assert task_id > 0

    tasks = tmp_db.get_tasks(status="pending")
    assert len(tasks) == 1
    assert tasks[0].title == "Review Q3 budget"
    assert tasks[0].priority == "high"
    assert tasks[0].id == task_id


def test_get_task_by_id(tmp_db):
    task = Task(title="Test task", source_type="event", source_id=1)
    task_id = tmp_db.add_task(task)

    retrieved = tmp_db.get_task(task_id)
    assert retrieved is not None
    assert retrieved.title == "Test task"
    assert retrieved.source_type == "event"


def test_get_task_nonexistent(tmp_db):
    assert tmp_db.get_task(9999) is None


def test_update_task_status(tmp_db):
    task = Task(title="Test task", source_type="event", source_id=1)
    task_id = tmp_db.add_task(task)

    assert tmp_db.update_task_status(task_id, "done") is True
    updated = tmp_db.get_task(task_id)
    assert updated.status == "done"


def test_update_task_status_dismiss(tmp_db):
    task = Task(title="Low priority", source_type="email", source_id=1, priority="low")
    task_id = tmp_db.add_task(task)

    assert tmp_db.update_task_status(task_id, "dismissed") is True
    updated = tmp_db.get_task(task_id)
    assert updated.status == "dismissed"


def test_update_nonexistent_task(tmp_db):
    assert tmp_db.update_task_status(9999, "done") is False


def test_task_priority_ordering(tmp_db):
    """Tasks should be ordered: high > medium > low."""
    for pri in ["low", "high", "medium"]:
        tmp_db.add_task(Task(
            title=f"{pri} task",
            source_type="email",
            source_id=1,
            priority=pri,
        ))

    tasks = tmp_db.get_tasks()
    assert tasks[0].priority == "high"
    assert tasks[1].priority == "medium"
    assert tasks[2].priority == "low"


def test_get_tasks_filter_by_status(tmp_db):
    tmp_db.add_task(Task(title="Pending", source_type="email", source_id=1))
    task_id = tmp_db.add_task(Task(title="Done", source_type="email", source_id=2))
    tmp_db.update_task_status(task_id, "done")

    pending = tmp_db.get_tasks(status="pending")
    assert len(pending) == 1
    assert pending[0].title == "Pending"

    done = tmp_db.get_tasks(status="done")
    assert len(done) == 1
    assert done[0].title == "Done"

    all_tasks = tmp_db.get_tasks()
    assert len(all_tasks) == 2


# --- Extraction tracking ---


def test_extraction_tracking(tmp_db):
    # Insert a test email
    email = Email(
        message_id="track-test@example.com",
        folder="INBOX",
        from_addr="alice@example.com",
        subject="Action needed",
        date_sent=datetime.now(),
    )
    email_id = tmp_db.upsert_email(email)

    # Should appear as unprocessed
    unprocessed = tmp_db.get_unprocessed_email_ids()
    assert email_id in unprocessed

    # Mark as processed
    tmp_db.mark_extracted("email", email_id, 1)

    # Should no longer appear
    unprocessed = tmp_db.get_unprocessed_email_ids()
    assert email_id not in unprocessed


def test_extraction_tracking_events(tmp_db):
    event = Event(
        uid="evt-track-test",
        calendar_name="Work",
        summary="Team standup",
        dtstart=datetime.now(),
    )
    event_id = tmp_db.upsert_event(event)

    unprocessed = tmp_db.get_unprocessed_event_ids()
    assert event_id in unprocessed

    tmp_db.mark_extracted("event", event_id, 0)

    unprocessed = tmp_db.get_unprocessed_event_ids()
    assert event_id not in unprocessed


def test_get_email_by_id(tmp_db):
    email = Email(
        message_id="byid-test@example.com",
        folder="INBOX",
        from_addr="bob@example.com",
        subject="Hello",
        date_sent=datetime.now(),
    )
    email_id = tmp_db.upsert_email(email)

    retrieved = tmp_db.get_email_by_id(email_id)
    assert retrieved is not None
    assert retrieved.message_id == "byid-test@example.com"
    assert retrieved.from_addr == "bob@example.com"


def test_get_event_by_id(tmp_db):
    event = Event(
        uid="byid-event-test",
        calendar_name="Personal",
        summary="Lunch",
        dtstart=datetime.now(),
    )
    event_id = tmp_db.upsert_event(event)

    retrieved = tmp_db.get_event_by_id(event_id)
    assert retrieved is not None
    assert retrieved.uid == "byid-event-test"
    assert retrieved.summary == "Lunch"
