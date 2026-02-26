"""Tests for VLM task queue API endpoints."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from giva.config import GivaConfig
from giva.db.models import Goal, VlmTask
from giva.db.store import Store


def _create_vlm_test_app(store: Store, config: GivaConfig) -> FastAPI:
    """Create a test-only FastAPI app with VLM endpoints."""
    from giva.server import (
        create_vlm_task,
        list_vlm_tasks,
        get_current_vlm_task,
        vlm_analyze,
        complete_vlm_task,
    )

    test_app = FastAPI()
    test_app.state.store = store
    test_app.state.config = config

    # Register VLM routes — put /current before /{param} to avoid conflicts
    test_app.get("/api/vlm/tasks/current")(get_current_vlm_task)
    test_app.post("/api/vlm/tasks")(create_vlm_task)
    test_app.get("/api/vlm/tasks")(list_vlm_tasks)
    test_app.post("/api/vlm/vision/analyze")(vlm_analyze)
    test_app.post("/api/vlm/tasks/complete")(complete_vlm_task)

    return test_app


@pytest.fixture
def vlm_app(tmp_path):
    store = Store(tmp_path / "test.db")
    config = GivaConfig(data_dir=tmp_path)
    # Create a goal for VLM tasks
    goal_id = store.add_goal(Goal(title="Test Goal", tier="short_term"))
    app = _create_vlm_test_app(store, config)
    return TestClient(app), store, goal_id


# --- POST /api/vlm/tasks ---


def test_create_vlm_task(vlm_app):
    client, store, goal_id = vlm_app
    resp = client.post("/api/vlm/tasks", json={
        "goal_id": goal_id,
        "objective": "Click the login button",
        "target_url": "https://example.com/login",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["objective"] == "Click the login button"
    assert data["status"] == "queued"
    assert data["task_uuid"]
    assert data["goal_id"] == goal_id


def test_create_vlm_task_with_job_id(vlm_app):
    client, store, goal_id = vlm_app
    resp = client.post("/api/vlm/tasks", json={
        "goal_id": goal_id,
        "objective": "Step 1",
        "target_url": "https://example.com",
        "job_id": "job-abc",
        "sequence": 0,
    })
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-abc"
    assert resp.json()["sequence"] == 0


# --- GET /api/vlm/tasks ---


def test_list_vlm_tasks_empty(vlm_app):
    client, _, _ = vlm_app
    resp = client.get("/api/vlm/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_vlm_tasks(vlm_app):
    client, store, goal_id = vlm_app
    for i in range(3):
        store.add_vlm_task(VlmTask(
            task_uuid=str(uuid.uuid4()),
            goal_id=goal_id,
            objective=f"Step {i}",
            target_url="https://example.com",
            sequence=i,
        ))

    resp = client.get("/api/vlm/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_list_vlm_tasks_by_status(vlm_app):
    client, store, goal_id = vlm_app
    t = VlmTask(
        task_uuid=str(uuid.uuid4()),
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
    )
    tid = store.add_vlm_task(t)
    store.update_vlm_task_status(tid, "completed")

    store.add_vlm_task(VlmTask(
        task_uuid=str(uuid.uuid4()),
        goal_id=goal_id,
        objective="Test2",
        target_url="https://example.com",
    ))

    resp = client.get("/api/vlm/tasks", params={"status": "queued"})
    assert len(resp.json()) == 1

    resp = client.get("/api/vlm/tasks", params={"status": "completed"})
    assert len(resp.json()) == 1


# --- GET /api/vlm/tasks/current ---


def test_current_vlm_task_empty(vlm_app):
    client, _, _ = vlm_app
    resp = client.get("/api/vlm/tasks/current")
    assert resp.status_code == 204


def test_current_vlm_task_returns_queued(vlm_app):
    client, store, goal_id = vlm_app
    store.add_vlm_task(VlmTask(
        task_uuid=str(uuid.uuid4()),
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
    ))

    resp = client.get("/api/vlm/tasks/current")
    assert resp.status_code == 200
    data = resp.json()
    # Auto-transitioned to in_progress
    assert data["status"] == "in_progress"


def test_current_vlm_task_auto_transitions(vlm_app):
    client, store, goal_id = vlm_app
    task_uuid = str(uuid.uuid4())
    store.add_vlm_task(VlmTask(
        task_uuid=task_uuid,
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
    ))

    # First poll transitions to in_progress
    resp = client.get("/api/vlm/tasks/current")
    assert resp.json()["status"] == "in_progress"

    # Verify DB was updated
    task = store.get_vlm_task_by_uuid(task_uuid)
    assert task.status == "in_progress"


# --- POST /api/vlm/vision/analyze ---


def test_vlm_analyze(vlm_app):
    client, store, goal_id = vlm_app
    task_uuid = str(uuid.uuid4())
    store.add_vlm_task(VlmTask(
        task_uuid=task_uuid,
        goal_id=goal_id,
        objective="Click login",
        target_url="https://example.com",
        status="in_progress",
    ))

    resp = client.post("/api/vlm/vision/analyze", json={
        "task_uuid": task_uuid,
        "screenshot_b64": "iVBORw0KGgo=",  # minimal base64 PNG stub
    })
    assert resp.status_code == 200
    data = resp.json()
    # Placeholder VLM returns "done"
    assert data["action_type"] == "done"
    assert "reasoning" in data


def test_vlm_analyze_task_not_found(vlm_app):
    client, _, _ = vlm_app
    resp = client.post("/api/vlm/vision/analyze", json={
        "task_uuid": "nonexistent",
        "screenshot_b64": "abc",
    })
    assert resp.status_code == 404


def test_vlm_analyze_wrong_status(vlm_app):
    client, store, goal_id = vlm_app
    task_uuid = str(uuid.uuid4())
    tid = store.add_vlm_task(VlmTask(
        task_uuid=task_uuid,
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
    ))
    store.update_vlm_task_status(tid, "completed")

    resp = client.post("/api/vlm/vision/analyze", json={
        "task_uuid": task_uuid,
        "screenshot_b64": "abc",
    })
    assert resp.status_code == 409


# --- POST /api/vlm/tasks/complete ---


def test_complete_vlm_task_success(vlm_app):
    client, store, goal_id = vlm_app
    task_uuid = str(uuid.uuid4())
    store.add_vlm_task(VlmTask(
        task_uuid=task_uuid,
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
        status="in_progress",
    ))

    resp = client.post("/api/vlm/tasks/complete", json={
        "task_uuid": task_uuid,
        "vlm_report": "Successfully clicked login button",
        "success": True,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["vlm_report"] == "Successfully clicked login button"


def test_complete_vlm_task_failure(vlm_app):
    client, store, goal_id = vlm_app
    task_uuid = str(uuid.uuid4())
    store.add_vlm_task(VlmTask(
        task_uuid=task_uuid,
        goal_id=goal_id,
        objective="Test",
        target_url="https://example.com",
        status="in_progress",
    ))

    resp = client.post("/api/vlm/tasks/complete", json={
        "task_uuid": task_uuid,
        "vlm_report": "Element not found",
        "success": False,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


def test_complete_nonexistent_task(vlm_app):
    client, _, _ = vlm_app
    resp = client.post("/api/vlm/tasks/complete", json={
        "task_uuid": "nonexistent",
        "vlm_report": "test",
    })
    assert resp.status_code == 404


# --- Multi-subtask flow ---


def test_multi_subtask_sequential(vlm_app):
    """Complete one subtask → next one becomes current."""
    client, store, goal_id = vlm_app
    job_id = str(uuid.uuid4())

    uuid1 = str(uuid.uuid4())
    uuid2 = str(uuid.uuid4())
    store.add_vlm_task(VlmTask(
        task_uuid=uuid1, goal_id=goal_id,
        objective="Step 1", target_url="https://example.com",
        job_id=job_id, sequence=0,
    ))
    store.add_vlm_task(VlmTask(
        task_uuid=uuid2, goal_id=goal_id,
        objective="Step 2", target_url="https://example.com",
        job_id=job_id, sequence=1,
    ))

    # First poll gets step 1
    resp = client.get("/api/vlm/tasks/current")
    assert resp.json()["task_uuid"] == uuid1
    assert resp.json()["sequence"] == 0

    # Complete step 1
    client.post("/api/vlm/tasks/complete", json={
        "task_uuid": uuid1,
        "vlm_report": "Step 1 done",
        "success": True,
    })

    # Next poll gets step 2
    resp = client.get("/api/vlm/tasks/current")
    assert resp.json()["task_uuid"] == uuid2
    assert resp.json()["sequence"] == 1
