"""Tests for the budget-aware context assembly module."""

from datetime import datetime, timedelta

from giva.config import LLMConfig
from giva.db.models import Email, Event, Task
from giva.intelligence.context import (
    _parse_param_count,
    effective_budget,
    estimate_tokens,
    format_task_context,
    retrieve_context,
    truncate_to_budget,
)


# --- _parse_param_count ---


def test_parse_param_count_standard():
    """Should parse standard model IDs."""
    assert _parse_param_count("mlx-community/Qwen3-30B-A3B-4bit") == 30.0
    assert _parse_param_count("mlx-community/Qwen3-8B-4bit") == 8.0


def test_parse_param_count_fractional():
    """Should parse fractional param counts."""
    assert _parse_param_count("mlx-community/Qwen3-0.6B-4bit") == 0.6


def test_parse_param_count_no_match():
    """Should return None for unparsable model IDs."""
    assert _parse_param_count("some-random-model") is None


def test_parse_param_count_large():
    """Should parse large model IDs."""
    assert _parse_param_count("mlx-community/Qwen3-80B-A22B-4bit") == 80.0


# --- effective_budget ---


def test_effective_budget_small_model():
    """Small model (≤1B) should get 2000 token budget."""
    config = LLMConfig(model="mlx-community/Qwen3-0.6B-4bit")
    assert effective_budget(config) == 2000


def test_effective_budget_medium_model():
    """Medium model (≤8B) should get 4000 token budget."""
    config = LLMConfig(model="mlx-community/Qwen3-8B-4bit")
    assert effective_budget(config) == 4000


def test_effective_budget_large_model():
    """Large model (≤32B) should get 8000 token budget."""
    config = LLMConfig(model="mlx-community/Qwen3-30B-A3B-4bit")
    assert effective_budget(config) == 8000


def test_effective_budget_xlarge_model():
    """Extra-large model (>32B) should get 12000 token budget."""
    config = LLMConfig(model="mlx-community/Qwen3-80B-A22B-4bit")
    assert effective_budget(config) == 12000


def test_effective_budget_unparsable_model():
    """Unparsable model ID should fall back to config default."""
    config = LLMConfig(model="some-random-model", context_budget_tokens=5000)
    assert effective_budget(config) == 5000


# --- estimate_tokens ---


def test_estimate_tokens_basic():
    """Should estimate ~4 chars per token."""
    assert estimate_tokens("") == 1  # min 1
    assert estimate_tokens("Hello world") == 3  # 11 chars / 4 + 1 = 3


def test_estimate_tokens_long_text():
    """Should scale linearly."""
    text = "a" * 400
    assert estimate_tokens(text) == 101  # 400 / 4 + 1


# --- truncate_to_budget ---


def test_truncate_short_text():
    """Short text should pass through unchanged."""
    assert truncate_to_budget("Hello", 100) == "Hello"


def test_truncate_long_text():
    """Long text should be cut with marker."""
    text = "a" * 1000
    result = truncate_to_budget(text, 10)  # 10 tokens = 40 chars
    assert len(result) < len(text)
    assert result.endswith("[...truncated]")


def test_truncate_empty():
    """Empty text should return empty."""
    assert truncate_to_budget("", 100) == ""


# --- format_task_context ---


def test_format_task_context_empty():
    """Should return empty string for no tasks."""
    assert format_task_context([]) == ""


def test_format_task_context_basic():
    """Should format tasks with priority and due date."""
    tasks = [
        Task(
            title="Call John",
            source_type="email",
            source_id=1,
            priority="high",
            due_date=datetime(2026, 3, 15),
        ),
        Task(
            title="Review PR",
            source_type="event",
            source_id=2,
            priority="medium",
        ),
    ]
    result = format_task_context(tasks)
    assert "## Pending Tasks" in result
    assert "[HIGH] Call John (due Mar 15)" in result
    assert "[medium] Review PR" in result


def test_format_task_context_with_goal():
    """Should show goal link."""
    tasks = [
        Task(
            title="Research competitors",
            source_type="manual",
            source_id=0,
            priority="low",
            goal_id=3,
        ),
    ]
    result = format_task_context(tasks)
    assert "[goal #3]" in result


def test_format_task_context_with_description():
    """Should include truncated description."""
    tasks = [
        Task(
            title="Write report",
            source_type="manual",
            source_id=0,
            description="A" * 200,
        ),
    ]
    result = format_task_context(tasks)
    assert "..." in result
    # Description line should be indented
    lines = result.split("\n")
    assert any(line.startswith("  A") for line in lines)


# --- retrieve_context ---


def test_retrieve_context_empty_db(tmp_db):
    """Should return fallback message with empty DB."""
    config = LLMConfig()
    result = retrieve_context("hello", tmp_db, config)
    assert "No emails, events, or tasks found" in result


def test_retrieve_context_with_tasks(tmp_db):
    """Should include pending tasks in context."""
    tmp_db.add_task(Task(
        title="Call Alice",
        source_type="manual",
        source_id=0,
        priority="high",
        due_date=datetime.now() + timedelta(days=1),
    ))
    config = LLMConfig()
    result = retrieve_context("tasks", tmp_db, config)
    assert "Call Alice" in result
    assert "Pending Tasks" in result


def test_retrieve_context_with_events(tmp_db):
    """Should include upcoming events."""
    tomorrow = datetime.now() + timedelta(days=1)
    tmp_db.upsert_event(Event(
        uid="evt-1", calendar_name="Work",
        summary="Team standup",
        dtstart=tomorrow.replace(hour=10, minute=0),
        dtend=tomorrow.replace(hour=10, minute=30),
    ))
    config = LLMConfig()
    result = retrieve_context("meeting", tmp_db, config)
    assert "Team standup" in result


def test_retrieve_context_respects_budget(tmp_db):
    """Context with small budget should be truncated."""
    # Add lots of emails
    for i in range(20):
        tmp_db.upsert_email(Email(
            message_id=f"msg-{i}@test",
            folder="INBOX",
            from_addr=f"user{i}@test.com",
            from_name=f"User {i}",
            subject=f"Important email number {i} with some extra text to pad",
            date_sent=datetime.now() - timedelta(hours=i),
            body_plain=f"Body content for email {i}. " * 50,
        ))
    # Use a very small model → tight budget
    config = LLMConfig(model="mlx-community/Qwen3-0.6B-4bit")
    result = retrieve_context("important", tmp_db, config)
    # Should have content but be much smaller than a big-model context
    assert len(result) > 0
    # The 0.6B budget is 2000 tokens * 0.55 = 1100 tokens ≈ 4400 chars
    # Context should be roughly bounded by this
    assert len(result) < 8000  # generous upper bound


# --- Store.update_task ---


def test_update_task_title(tmp_db):
    """update_task should update the title."""
    task_id = tmp_db.add_task(Task(
        title="Old title",
        source_type="manual",
        source_id=0,
    ))
    assert tmp_db.update_task(task_id, title="New title") is True
    updated = tmp_db.get_task(task_id)
    assert updated.title == "New title"


def test_update_task_multiple_fields(tmp_db):
    """update_task should update multiple fields at once."""
    task_id = tmp_db.add_task(Task(
        title="Original",
        source_type="manual",
        source_id=0,
        priority="low",
    ))
    assert tmp_db.update_task(
        task_id, priority="high", description="Updated desc", status="in_progress"
    ) is True
    updated = tmp_db.get_task(task_id)
    assert updated.priority == "high"
    assert updated.description == "Updated desc"
    assert updated.status == "in_progress"


def test_update_task_due_date(tmp_db):
    """update_task should handle datetime due_date."""
    task_id = tmp_db.add_task(Task(
        title="Test",
        source_type="manual",
        source_id=0,
    ))
    due = datetime(2026, 6, 15)
    assert tmp_db.update_task(task_id, due_date=due) is True
    updated = tmp_db.get_task(task_id)
    assert updated.due_date is not None
    assert updated.due_date.year == 2026
    assert updated.due_date.month == 6


def test_update_task_clear_due_date(tmp_db):
    """update_task should clear due_date when set to None."""
    task_id = tmp_db.add_task(Task(
        title="Test",
        source_type="manual",
        source_id=0,
        due_date=datetime(2026, 6, 15),
    ))
    assert tmp_db.update_task(task_id, due_date=None) is True
    updated = tmp_db.get_task(task_id)
    assert updated.due_date is None


def test_update_task_goal_id(tmp_db):
    """update_task should update goal_id."""
    from giva.db.models import Goal

    goal_id = tmp_db.add_goal(Goal(
        title="Career growth", tier="long_term", category="career",
    ))
    task_id = tmp_db.add_task(Task(
        title="Research jobs",
        source_type="manual",
        source_id=0,
    ))
    assert tmp_db.update_task(task_id, goal_id=goal_id) is True
    updated = tmp_db.get_task(task_id)
    assert updated.goal_id == goal_id


def test_update_task_not_found(tmp_db):
    """update_task should return False for nonexistent task."""
    assert tmp_db.update_task(9999, title="Nope") is False


def test_update_task_no_fields(tmp_db):
    """update_task should return False when no valid fields given."""
    task_id = tmp_db.add_task(Task(
        title="Test",
        source_type="manual",
        source_id=0,
    ))
    assert tmp_db.update_task(task_id, bogus_field="nope") is False


def test_update_task_ignores_disallowed_fields(tmp_db):
    """update_task should ignore fields like source_type, source_id."""
    task_id = tmp_db.add_task(Task(
        title="Test",
        source_type="manual",
        source_id=0,
    ))
    # source_type is not in the allowed set
    assert tmp_db.update_task(task_id, source_type="email") is False
    updated = tmp_db.get_task(task_id)
    assert updated.source_type == "manual"
