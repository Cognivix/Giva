"""Tests for Pydantic structured output models."""

from giva.llm.structured import ExtractedTask, TaskExtractionResult, Priority


def test_extracted_task_defaults():
    t = ExtractedTask(title="Do something")
    assert t.priority == Priority.medium
    assert t.due_date is None
    assert t.source_quote == ""
    assert t.description is None


def test_extracted_task_with_all_fields():
    t = ExtractedTask(
        title="Review proposal",
        description="The Q3 budget proposal needs sign-off",
        priority=Priority.high,
        due_date="2026-03-01",
        source_quote="Please review by March 1st",
    )
    assert t.title == "Review proposal"
    assert t.priority == Priority.high
    assert t.due_date == "2026-03-01"


def test_task_extraction_result_from_dict():
    raw = {
        "tasks": [
            {"title": "Send report", "priority": "high", "due_date": "2026-03-01"},
            {"title": "Follow up", "description": "With Bob about contract"},
        ],
        "has_actionable_items": True,
    }
    result = TaskExtractionResult.model_validate(raw)
    assert len(result.tasks) == 2
    assert result.tasks[0].priority == Priority.high
    assert result.tasks[1].priority == Priority.medium  # default


def test_task_extraction_result_empty():
    result = TaskExtractionResult()
    assert len(result.tasks) == 0
    assert result.has_actionable_items is False


def test_priority_enum_values():
    assert Priority.high.value == "high"
    assert Priority.medium.value == "medium"
    assert Priority.low.value == "low"


def test_task_extraction_result_no_actionable():
    raw = {"tasks": [], "has_actionable_items": False}
    result = TaskExtractionResult.model_validate(raw)
    assert len(result.tasks) == 0
    assert result.has_actionable_items is False
