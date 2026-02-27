"""Tests for VLM task queue data layer."""

import uuid

import pytest

from giva.db.models import Goal, VlmTask


@pytest.fixture
def goal_id(tmp_db):
    """Create a goal for VLM tasks to reference."""
    return tmp_db.add_goal(Goal(title="Test Goal", tier="short_term"))


def _make_vlm_task(**kwargs):
    defaults = {
        "task_uuid": str(uuid.uuid4()),
        "goal_id": 1,
        "objective": "Click the login button",
        "target_url": "https://example.com",
    }
    defaults.update(kwargs)
    return VlmTask(**defaults)


# --- CRUD ---


def test_add_and_get_vlm_task(tmp_db, goal_id):
    task = _make_vlm_task(goal_id=goal_id)
    task_id = tmp_db.add_vlm_task(task)
    assert task_id > 0

    retrieved = tmp_db.get_vlm_task(task_id)
    assert retrieved is not None
    assert retrieved.objective == "Click the login button"
    assert retrieved.target_url == "https://example.com"
    assert retrieved.status == "queued"
    assert retrieved.goal_id == goal_id


def test_get_vlm_task_by_uuid(tmp_db, goal_id):
    task_uuid = str(uuid.uuid4())
    task = _make_vlm_task(goal_id=goal_id, task_uuid=task_uuid)
    tmp_db.add_vlm_task(task)

    retrieved = tmp_db.get_vlm_task_by_uuid(task_uuid)
    assert retrieved is not None
    assert retrieved.task_uuid == task_uuid


def test_get_vlm_task_by_uuid_not_found(tmp_db):
    result = tmp_db.get_vlm_task_by_uuid("nonexistent")
    assert result is None


def test_get_vlm_task_not_found(tmp_db):
    result = tmp_db.get_vlm_task(9999)
    assert result is None


# --- Current Task ---


def test_get_current_vlm_task_empty(tmp_db):
    result = tmp_db.get_current_vlm_task()
    assert result is None


def test_get_current_vlm_task_queued(tmp_db, goal_id):
    task = _make_vlm_task(goal_id=goal_id)
    tmp_db.add_vlm_task(task)

    current = tmp_db.get_current_vlm_task()
    assert current is not None
    assert current.status == "queued"


def test_get_current_vlm_task_prefers_in_progress(tmp_db, goal_id):
    # Add a queued task
    queued = _make_vlm_task(goal_id=goal_id, sequence=0)
    tmp_db.add_vlm_task(queued)

    # Add an in_progress task
    in_progress = _make_vlm_task(goal_id=goal_id, sequence=1, status="in_progress")
    ip_id = tmp_db.add_vlm_task(in_progress)

    current = tmp_db.get_current_vlm_task()
    assert current is not None
    assert current.id == ip_id
    assert current.status == "in_progress"


def test_get_current_vlm_task_respects_sequence(tmp_db, goal_id):
    job = str(uuid.uuid4())
    # Add tasks out of order
    t2 = _make_vlm_task(goal_id=goal_id, job_id=job, sequence=1)
    tmp_db.add_vlm_task(t2)
    t1 = _make_vlm_task(goal_id=goal_id, job_id=job, sequence=0)
    t1_id = tmp_db.add_vlm_task(t1)

    current = tmp_db.get_current_vlm_task()
    assert current is not None
    assert current.id == t1_id
    assert current.sequence == 0


# --- Status Updates ---


def test_update_vlm_task_status(tmp_db, goal_id):
    task = _make_vlm_task(goal_id=goal_id)
    task_id = tmp_db.add_vlm_task(task)

    success = tmp_db.update_vlm_task_status(task_id, "in_progress")
    assert success is True

    updated = tmp_db.get_vlm_task(task_id)
    assert updated.status == "in_progress"


def test_update_vlm_task_status_with_report(tmp_db, goal_id):
    task = _make_vlm_task(goal_id=goal_id)
    task_id = tmp_db.add_vlm_task(task)

    tmp_db.update_vlm_task_status(
        task_id, "completed", vlm_report="Successfully clicked login"
    )

    updated = tmp_db.get_vlm_task(task_id)
    assert updated.status == "completed"
    assert updated.vlm_report == "Successfully clicked login"


def test_update_vlm_task_status_with_error(tmp_db, goal_id):
    task = _make_vlm_task(goal_id=goal_id)
    task_id = tmp_db.add_vlm_task(task)

    tmp_db.update_vlm_task_status(
        task_id, "failed", error_message="Element not found"
    )

    updated = tmp_db.get_vlm_task(task_id)
    assert updated.status == "failed"
    assert updated.error_message == "Element not found"


def test_update_nonexistent_task(tmp_db):
    result = tmp_db.update_vlm_task_status(9999, "completed")
    assert result is False


# --- Listing ---


def test_get_vlm_tasks_all(tmp_db, goal_id):
    for i in range(3):
        tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id, sequence=i))

    tasks = tmp_db.get_vlm_tasks()
    assert len(tasks) == 3


def test_get_vlm_tasks_by_status(tmp_db, goal_id):
    t1 = _make_vlm_task(goal_id=goal_id)
    t1_id = tmp_db.add_vlm_task(t1)
    tmp_db.update_vlm_task_status(t1_id, "completed")

    tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id))

    queued = tmp_db.get_vlm_tasks(status="queued")
    assert len(queued) == 1

    completed = tmp_db.get_vlm_tasks(status="completed")
    assert len(completed) == 1


def test_get_vlm_tasks_by_job_id(tmp_db, goal_id):
    job1 = str(uuid.uuid4())
    job2 = str(uuid.uuid4())

    tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id, job_id=job1, sequence=0))
    tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id, job_id=job1, sequence=1))
    tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id, job_id=job2, sequence=0))

    job1_tasks = tmp_db.get_vlm_tasks(job_id=job1)
    assert len(job1_tasks) == 2

    job2_tasks = tmp_db.get_vlm_tasks(job_id=job2)
    assert len(job2_tasks) == 1


def test_get_vlm_tasks_with_limit(tmp_db, goal_id):
    for i in range(5):
        tmp_db.add_vlm_task(_make_vlm_task(goal_id=goal_id, sequence=i))

    tasks = tmp_db.get_vlm_tasks(limit=3)
    assert len(tasks) == 3


# --- Status Transitions ---


def test_full_lifecycle(tmp_db, goal_id):
    """Test queued → in_progress → completed lifecycle."""
    task = _make_vlm_task(goal_id=goal_id)
    task_id = tmp_db.add_vlm_task(task)

    # Starts as queued
    t = tmp_db.get_vlm_task(task_id)
    assert t.status == "queued"

    # Transition to in_progress
    tmp_db.update_vlm_task_status(task_id, "in_progress")
    t = tmp_db.get_vlm_task(task_id)
    assert t.status == "in_progress"

    # Complete with report
    tmp_db.update_vlm_task_status(
        task_id, "completed", vlm_report="All done"
    )
    t = tmp_db.get_vlm_task(task_id)
    assert t.status == "completed"
    assert t.vlm_report == "All done"
    assert t.updated_at is not None


# --- VlmTask model ---


def test_vlm_task_to_row():
    task = VlmTask(
        task_uuid="abc-123",
        goal_id=1,
        objective="Test",
        target_url="https://example.com",
        job_id="job-1",
        sequence=2,
    )
    row = task.to_row()
    assert row["task_uuid"] == "abc-123"
    assert row["goal_id"] == 1
    assert row["sequence"] == 2
    assert row["status"] == "queued"
