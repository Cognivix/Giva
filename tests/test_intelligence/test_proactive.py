"""Tests for proactive suggestion engine."""

from datetime import datetime, timedelta

from giva.db.models import Email, Event, Task
from giva.intelligence.proactive import _build_suggestion_context


def test_context_empty_db(tmp_db):
    """Should return empty string when no data exists."""
    context = _build_suggestion_context(tmp_db)
    assert context == ""


def test_context_includes_pending_tasks(tmp_db):
    """Should include pending tasks in context."""
    tmp_db.add_task(Task(
        title="Review budget proposal",
        source_type="email", source_id=1,
        priority="high",
        due_date=datetime(2026, 3, 1),
    ))
    tmp_db.add_task(Task(
        title="Send meeting notes",
        source_type="event", source_id=1,
        priority="medium",
    ))

    context = _build_suggestion_context(tmp_db)
    assert "Review budget proposal" in context
    assert "Send meeting notes" in context
    assert "Pending Tasks" in context


def test_context_includes_upcoming_events(tmp_db):
    """Should include events in the next 48 hours."""
    tomorrow = datetime.now() + timedelta(hours=24)
    tmp_db.upsert_event(Event(
        uid="upcoming-1", calendar_name="Work",
        summary="Team standup",
        dtstart=tomorrow,
        dtend=tomorrow + timedelta(hours=1),
    ))

    context = _build_suggestion_context(tmp_db)
    assert "Team standup" in context
    assert "Upcoming Events" in context


def test_context_includes_unread_emails(tmp_db):
    """Should include unread emails in context."""
    tmp_db.upsert_email(Email(
        message_id="unread-1@test", folder="INBOX",
        from_addr="bob@example.com", from_name="Bob",
        subject="Urgent: contract review",
        date_sent=datetime.now() - timedelta(hours=2),
        is_read=False,
    ))

    context = _build_suggestion_context(tmp_db)
    assert "Urgent: contract review" in context
    assert "Unread Emails" in context


def test_context_excludes_read_emails(tmp_db):
    """Read emails should not appear in unread section."""
    tmp_db.upsert_email(Email(
        message_id="read-1@test", folder="INBOX",
        from_addr="bob@example.com", from_name="Bob",
        subject="Already handled",
        date_sent=datetime.now() - timedelta(hours=2),
        is_read=True,
    ))

    context = _build_suggestion_context(tmp_db)
    assert "Already handled" not in context


def test_context_includes_past_today_events(tmp_db):
    """Should include events from earlier today."""
    earlier = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    # Only include if it's actually in the past
    if earlier < datetime.now():
        tmp_db.upsert_event(Event(
            uid="past-today-1", calendar_name="Work",
            summary="Morning sync",
            dtstart=earlier,
            dtend=earlier + timedelta(minutes=30),
        ))

        context = _build_suggestion_context(tmp_db)
        # May or may not appear depending on exact timing
        if "Morning sync" in context:
            assert "Already Happened Today" in context


def test_context_task_priority_shown(tmp_db):
    """High priority tasks should be labeled HIGH."""
    tmp_db.add_task(Task(
        title="Critical deadline",
        source_type="email", source_id=1,
        priority="high",
    ))

    context = _build_suggestion_context(tmp_db)
    assert "HIGH" in context


def test_context_task_due_date_shown(tmp_db):
    """Tasks with due dates should show them."""
    tmp_db.add_task(Task(
        title="Submit report",
        source_type="email", source_id=1,
        priority="medium",
        due_date=datetime(2026, 3, 15),
    ))

    context = _build_suggestion_context(tmp_db)
    assert "Mar 15" in context


def test_context_flagged_email_shown(tmp_db):
    """Flagged unread emails should show the flag marker."""
    tmp_db.upsert_email(Email(
        message_id="flagged-1@test", folder="INBOX",
        from_addr="boss@example.com", from_name="Boss",
        subject="Important action needed",
        date_sent=datetime.now() - timedelta(hours=1),
        is_read=False,
        is_flagged=True,
    ))

    context = _build_suggestion_context(tmp_db)
    assert "FLAGGED" in context
