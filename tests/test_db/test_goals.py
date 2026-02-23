"""Tests for goal, strategy, progress, and daily review data layer."""

from datetime import datetime

import pytest

from giva.db.models import (
    DailyReview,
    Goal,
    GoalStrategy,
    Task,
)


# --- Goal CRUD ---


def test_add_and_get_goal(tmp_db):
    goal = Goal(title="Become CTO", tier="long_term", category="career", priority="high")
    goal_id = tmp_db.add_goal(goal)
    assert goal_id > 0

    retrieved = tmp_db.get_goal(goal_id)
    assert retrieved is not None
    assert retrieved.title == "Become CTO"
    assert retrieved.tier == "long_term"
    assert retrieved.category == "career"
    assert retrieved.priority == "high"
    assert retrieved.status == "active"


def test_get_goals_filtered(tmp_db):
    tmp_db.add_goal(Goal(title="Career goal", tier="long_term", category="career"))
    tmp_db.add_goal(Goal(title="Health goal", tier="mid_term", category="health"))
    tmp_db.add_goal(
        Goal(title="Paused goal", tier="long_term", category="personal", status="paused")
    )

    all_active = tmp_db.get_goals()
    assert len(all_active) == 2

    long_term = tmp_db.get_goals(tier="long_term")
    assert len(long_term) == 1
    assert long_term[0].title == "Career goal"

    mid_term = tmp_db.get_goals(tier="mid_term")
    assert len(mid_term) == 1

    paused = tmp_db.get_goals(status="paused")
    assert len(paused) == 1
    assert paused[0].title == "Paused goal"


def test_goal_parent_child(tmp_db):
    parent_id = tmp_db.add_goal(Goal(title="Found a startup", tier="long_term"))
    child_id = tmp_db.add_goal(
        Goal(title="Write business plan", tier="mid_term", parent_id=parent_id)
    )

    children = tmp_db.get_child_goals(parent_id)
    assert len(children) == 1
    assert children[0].id == child_id
    assert children[0].parent_id == parent_id


def test_update_goal(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Old title", tier="long_term"))

    updated = tmp_db.update_goal(goal_id, title="New title", description="Updated")
    assert updated is True

    goal = tmp_db.get_goal(goal_id)
    assert goal.title == "New title"
    assert goal.description == "Updated"


def test_update_goal_status(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="short_term"))
    assert tmp_db.update_goal_status(goal_id, "completed")

    goal = tmp_db.get_goal(goal_id)
    assert goal.status == "completed"


def test_update_nonexistent_goal(tmp_db):
    assert tmp_db.update_goal(9999, title="nope") is False


def test_goal_target_date(tmp_db):
    target = datetime(2027, 6, 15)
    goal_id = tmp_db.add_goal(
        Goal(title="With target", tier="long_term", target_date=target)
    )
    goal = tmp_db.get_goal(goal_id)
    assert goal.target_date is not None
    assert goal.target_date.year == 2027


# --- Goal Progress ---


def test_add_and_get_progress(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="long_term"))

    pid = tmp_db.add_goal_progress(goal_id, "Made first investor contact", "user")
    assert pid > 0

    tmp_db.add_goal_progress(goal_id, "Synced email about funding", "sync")

    progress = tmp_db.get_goal_progress(goal_id)
    assert len(progress) == 2
    # Most recent first
    assert progress[0].source == "sync"
    assert progress[1].source == "user"
    assert progress[1].note == "Made first investor contact"


def test_progress_cascade_delete(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="To delete", tier="short_term"))
    tmp_db.add_goal_progress(goal_id, "Some progress", "user")

    # Delete the goal — progress should cascade
    with tmp_db._conn() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))

    progress = tmp_db.get_goal_progress(goal_id)
    assert len(progress) == 0


# --- Goal Strategies ---


def test_add_and_get_strategy(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Career", tier="long_term"))

    strategy = GoalStrategy(
        goal_id=goal_id,
        strategy_text="Network with senior leaders",
        action_items=[
            {"description": "Attend leadership meetups", "timeframe": "this month"},
            {"description": "Request 1:1 with VP", "timeframe": "this week"},
        ],
    )
    sid = tmp_db.add_strategy(strategy)
    assert sid > 0

    strategies = tmp_db.get_strategies(goal_id)
    assert len(strategies) == 1
    assert strategies[0].strategy_text == "Network with senior leaders"
    assert len(strategies[0].action_items) == 2
    assert strategies[0].status == "proposed"


def test_strategy_status_lifecycle(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="long_term"))
    sid = tmp_db.add_strategy(GoalStrategy(goal_id=goal_id, strategy_text="Plan A"))

    assert tmp_db.update_strategy_status(sid, "accepted")
    strategies = tmp_db.get_strategies(goal_id, status="accepted")
    assert len(strategies) == 1

    # Add another and supersede the first
    sid2 = tmp_db.add_strategy(GoalStrategy(goal_id=goal_id, strategy_text="Plan B"))
    tmp_db.update_strategy_status(sid, "superseded")
    tmp_db.update_strategy_status(sid2, "accepted")

    accepted = tmp_db.get_strategies(goal_id, status="accepted")
    assert len(accepted) == 1
    assert accepted[0].strategy_text == "Plan B"


# --- Daily Reviews ---


def test_add_and_get_review(tmp_db):
    review = DailyReview(
        review_date="2026-02-22",
        prompt_text="Here is your daily review...",
    )
    rid = tmp_db.add_daily_review(review)
    assert rid > 0

    retrieved = tmp_db.get_daily_review("2026-02-22")
    assert retrieved is not None
    assert retrieved.prompt_text.startswith("Here is")
    assert retrieved.user_response == ""


def test_update_review(tmp_db):
    review = DailyReview(review_date="2026-02-22", prompt_text="Review prompt")
    rid = tmp_db.add_daily_review(review)

    updated = tmp_db.update_daily_review(rid, "I completed tasks A and B", "Good progress")
    assert updated is True

    retrieved = tmp_db.get_daily_review("2026-02-22")
    assert retrieved.user_response == "I completed tasks A and B"
    assert retrieved.summary == "Good progress"


def test_review_unique_per_date(tmp_db):
    tmp_db.add_daily_review(DailyReview(review_date="2026-02-22", prompt_text="First"))
    with pytest.raises(Exception):  # UNIQUE constraint
        tmp_db.add_daily_review(DailyReview(review_date="2026-02-22", prompt_text="Dupe"))


def test_recent_reviews(tmp_db):
    for i in range(3):
        tmp_db.add_daily_review(
            DailyReview(review_date=f"2026-02-{20 + i:02d}", prompt_text=f"Review {i}")
        )
    reviews = tmp_db.get_recent_reviews(limit=2)
    assert len(reviews) == 2
    assert reviews[0].review_date == "2026-02-22"  # Most recent first


# --- Tasks linked to Goals ---


def test_task_goal_link(tmp_db):
    goal_id = tmp_db.add_goal(Goal(title="Career goal", tier="long_term"))
    task = Task(
        title="Update resume",
        source_type="email",
        source_id=1,
        goal_id=goal_id,
    )
    tid = tmp_db.add_task(task)
    assert tid > 0

    tasks = tmp_db.get_tasks_for_goal(goal_id)
    assert len(tasks) == 1
    assert tasks[0].title == "Update resume"
    assert tasks[0].goal_id == goal_id


def test_task_without_goal(tmp_db):
    task = Task(title="Standalone task", source_type="email", source_id=1)
    tid = tmp_db.add_task(task)

    t = tmp_db.get_task(tid)
    assert t.goal_id is None


# --- Stats includes goals ---


def test_stats_includes_goals(tmp_db):
    tmp_db.add_goal(Goal(title="G1", tier="long_term"))
    tmp_db.add_goal(Goal(title="G2", tier="mid_term"))
    stats = tmp_db.get_stats()
    assert stats["active_goals"] == 2


# --- Reset clears goals ---


def test_reset_clears_goals(tmp_db):
    gid = tmp_db.add_goal(Goal(title="G1", tier="long_term"))
    tmp_db.add_goal_progress(gid, "note", "user")
    tmp_db.add_strategy(GoalStrategy(goal_id=gid, strategy_text="plan"))
    tmp_db.add_daily_review(DailyReview(review_date="2026-01-01", prompt_text="rev"))

    tmp_db.reset_all_data()

    assert tmp_db.get_goals() == []
    assert tmp_db.get_goal_progress(gid) == []
    assert tmp_db.get_strategies(gid) == []
    assert tmp_db.get_recent_reviews() == []


# --- Goal-Scoped Conversations ---


def test_add_message_with_goal_id(tmp_db):
    """Goal chat messages are persisted with the goal_id."""
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="long_term"))
    tmp_db.add_message("user", "How do I achieve this?", goal_id=goal_id)
    tmp_db.add_message("assistant", "Here's a plan...", goal_id=goal_id)

    msgs = tmp_db.get_recent_messages(limit=10, goal_id=goal_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "How do I achieve this?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Here's a plan..."


def test_goal_messages_isolated_from_global(tmp_db):
    """Goal-scoped messages don't appear in global chat and vice versa."""
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="long_term"))

    # Add global message
    tmp_db.add_message("user", "global message")
    # Add goal-scoped message
    tmp_db.add_message("user", "goal message", goal_id=goal_id)

    global_msgs = tmp_db.get_recent_messages(limit=10)
    assert len(global_msgs) == 1
    assert global_msgs[0]["content"] == "global message"

    goal_msgs = tmp_db.get_recent_messages(limit=10, goal_id=goal_id)
    assert len(goal_msgs) == 1
    assert goal_msgs[0]["content"] == "goal message"


def test_goal_messages_cascade_on_delete(tmp_db):
    """Deleting a goal cascades to its chat messages."""
    goal_id = tmp_db.add_goal(Goal(title="Deletable", tier="short_term"))
    tmp_db.add_message("user", "some chat", goal_id=goal_id)
    tmp_db.add_message("assistant", "a response", goal_id=goal_id)

    # Verify messages exist
    assert len(tmp_db.get_goal_messages(goal_id)) == 2

    # Delete the goal
    with tmp_db._conn() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))

    # Messages should be gone
    assert len(tmp_db.get_goal_messages(goal_id)) == 0


def test_get_goal_messages_convenience(tmp_db):
    """get_goal_messages returns messages scoped to the goal."""
    goal_id = tmp_db.add_goal(Goal(title="Test", tier="long_term"))
    tmp_db.add_message("user", "q1", goal_id=goal_id)
    tmp_db.add_message("assistant", "a1", goal_id=goal_id)
    tmp_db.add_message("user", "q2", goal_id=goal_id)

    msgs = tmp_db.get_goal_messages(goal_id)
    assert len(msgs) == 3
    # Oldest first
    assert msgs[0]["content"] == "q1"
    assert msgs[2]["content"] == "q2"


def test_multiple_goals_separate_conversations(tmp_db):
    """Different goals have independent chat histories."""
    g1 = tmp_db.add_goal(Goal(title="Goal A", tier="long_term"))
    g2 = tmp_db.add_goal(Goal(title="Goal B", tier="mid_term"))

    tmp_db.add_message("user", "about goal A", goal_id=g1)
    tmp_db.add_message("user", "about goal B", goal_id=g2)
    tmp_db.add_message("assistant", "reply to A", goal_id=g1)

    assert len(tmp_db.get_goal_messages(g1)) == 2
    assert len(tmp_db.get_goal_messages(g2)) == 1
    # Global should have neither
    assert len(tmp_db.get_recent_messages(limit=10)) == 0
