"""Extended tests for the post-chat agent pipeline.

Tests cover:
- _handle_create_task (with goal auto-linking and duplicate detection)
- _handle_create_objective (with tier inference and enrichment/decomposition)
- _handle_complete_task (title matching)
- _handle_preference (fact storage)
- _auto_link_goal (keyword overlap matching)
- _enrich_objective_description (context extraction from conversation)
- _decompose_objective_to_tasks (task decomposition from conversation)
- run_post_chat_agent (full pipeline with mocked LLM)
"""

from unittest.mock import patch

from giva.db.models import Goal, Task, UserProfile
from giva.intelligence.agents import (
    _auto_link_goal,
    _decompose_objective_to_tasks,
    _enrich_objective_description,
    _handle_complete_task,
    _handle_create_objective,
    _handle_create_task,
    _handle_preference,
    run_post_chat_agent,
)


class TestHandleCreateTask:

    def test_creates_task(self, tmp_db):
        intent = {"title": "Buy groceries", "description": "Milk and eggs", "priority": "low"}
        action = _handle_create_task(intent, tmp_db)
        assert action is not None
        assert action["type"] == "task_created"
        assert action["title"] == "Buy groceries"
        assert action["priority"] == "low"

        # Verify DB
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0].source_type == "chat"

    def test_skips_duplicate_title(self, tmp_db):
        tmp_db.add_task(Task(title="Buy groceries", source_type="chat", source_id=0))
        intent = {"title": "Buy groceries"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_case_insensitive_duplicate(self, tmp_db):
        tmp_db.add_task(Task(title="Buy Groceries", source_type="chat", source_id=0))
        intent = {"title": "buy groceries"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_no_title_returns_none(self, tmp_db):
        intent = {"description": "Something"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_auto_links_to_goal_by_context(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(title="Health fitness", tier="long_term"))
        intent = {"title": "Go to gym"}
        action = _handle_create_task(intent, tmp_db, goal_id=goal_id)
        assert action is not None
        assert action["goal_id"] == goal_id

    def test_default_priority_medium(self, tmp_db):
        intent = {"title": "Test task"}
        action = _handle_create_task(intent, tmp_db)
        assert action["priority"] == "medium"


class TestHandleCreateObjective:

    def test_creates_objective(self, tmp_db):
        intent = {"title": "Launch MVP", "description": "First version", "tier": "mid_term"}
        action = _handle_create_objective(intent, tmp_db)
        assert action is not None
        assert action["type"] == "objective_created"
        assert action["title"] == "Launch MVP"
        assert action["tier"] == "mid_term"

    def test_infers_tier_from_long_term_parent(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Career growth", tier="long_term"))
        intent = {"title": "Get promotion"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action["tier"] == "mid_term"
        assert action["parent_id"] == parent_id

    def test_infers_tier_from_mid_term_parent(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Launch product", tier="mid_term"))
        intent = {"title": "Write docs"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action["tier"] == "short_term"

    def test_short_term_parent_detaches(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Quick task", tier="short_term"))
        intent = {"title": "Sub item"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action is not None
        assert action["parent_id"] is None

    def test_skips_duplicate_child(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Career", tier="long_term"))
        tmp_db.add_goal(Goal(
            title="Get promotion", tier="mid_term", parent_id=parent_id,
        ))
        intent = {"title": "Get promotion"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action is None

    def test_no_title_returns_none(self, tmp_db):
        intent = {"description": "Something"}
        action = _handle_create_objective(intent, tmp_db)
        assert action is None


class TestAutoLinkGoal:

    def test_links_by_keyword_overlap(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(
            title="Learn Python programming", tier="long_term", category="education",
        ))
        result = _auto_link_goal("Practice Python exercises", "", tmp_db)
        assert result == goal_id

    def test_no_overlap_returns_none(self, tmp_db):
        tmp_db.add_goal(Goal(title="Learn cooking", tier="long_term"))
        result = _auto_link_goal("Fix car engine", "", tmp_db)
        assert result is None

    def test_no_goals_returns_none(self, tmp_db):
        result = _auto_link_goal("Some task", "", tmp_db)
        assert result is None

    def test_best_match_wins(self, tmp_db):
        tmp_db.add_goal(Goal(title="Learn Python", tier="long_term"))
        goal2 = tmp_db.add_goal(Goal(
            title="Master Python data science", tier="long_term", category="data",
        ))
        result = _auto_link_goal("Python data analysis project", "", tmp_db)
        assert result == goal2

    def test_short_words_ignored(self, tmp_db):
        tmp_db.add_goal(Goal(title="Do it", tier="short_term"))
        result = _auto_link_goal("Do it now", "", tmp_db)
        assert result is None  # Words "do", "it" are <=2 chars


class TestHandleCompleteTask:

    def test_completes_matching_task(self, tmp_db):
        t_id = tmp_db.add_task(Task(
            title="Review the report", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "review the report"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is not None
        assert action["type"] == "task_completed"
        assert action["task_id"] == t_id

    def test_partial_title_match(self, tmp_db):
        t_id = tmp_db.add_task(Task(
            title="Review Q3 financial report", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "review q3 financial report"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is not None

    def test_no_match_returns_none(self, tmp_db):
        tmp_db.add_task(Task(
            title="Completely different task", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "something unrelated xyz"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is None

    def test_empty_title_returns_none(self, tmp_db):
        intent = {"title": ""}
        action = _handle_complete_task(intent, tmp_db, [])
        assert action is None


class TestHandlePreference:

    def test_saves_new_preference(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com",
            profile_data={"learned_facts": []},
        )
        tmp_db.upsert_profile(profile)

        intent = {"detail": "Prefers morning meetings"}
        action = _handle_preference(intent, tmp_db)
        assert action is not None
        assert action["type"] == "preference_saved"
        assert action["detail"] == "Prefers morning meetings"

        # Verify stored
        updated = tmp_db.get_profile()
        assert "Prefers morning meetings" in updated.profile_data["learned_facts"]

    def test_skips_duplicate_preference(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com",
            profile_data={"learned_facts": ["Prefers morning meetings"]},
        )
        tmp_db.upsert_profile(profile)

        intent = {"detail": "Prefers morning meetings"}
        action = _handle_preference(intent, tmp_db)
        assert action is None

    def test_no_profile_returns_none(self, tmp_db):
        intent = {"detail": "Some preference"}
        action = _handle_preference(intent, tmp_db)
        assert action is None

    def test_empty_detail_returns_none(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com", profile_data={},
        )
        tmp_db.upsert_profile(profile)
        intent = {"detail": ""}
        action = _handle_preference(intent, tmp_db)
        assert action is None


class TestRunPostChatAgent:

    @patch("giva.llm.engine.manager")
    def test_creates_task_from_llm_response(self, mock_manager, tmp_db, config):
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "create_task", "title": "Send invoice", '
            '"description": "Due Friday", "priority": "high"}], '
            '"topic": "billing", "progress": []}'
        )
        actions = run_post_chat_agent(
            "I need to send the invoice", "Sure, I'll help.", tmp_db, config,
        )
        assert len(actions) == 1
        assert actions[0]["type"] == "task_created"
        assert actions[0]["title"] == "Send invoice"

    @patch("giva.llm.engine.manager")
    def test_logs_progress(self, mock_manager, tmp_db, config):
        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "none"}], "topic": "progress", '
            f'"progress": [{{"goal_id": {goal_id}, "note": "Finished API"}}]}}'
        )
        actions = run_post_chat_agent(
            "I finished the API", "Great job!", tmp_db, config,
        )
        assert any(a["type"] == "goal_progress" for a in actions)

    @patch("giva.llm.engine.manager")
    def test_handles_llm_error_gracefully(self, mock_manager, tmp_db, config):
        mock_manager.generate.side_effect = RuntimeError("LLM crashed")
        actions = run_post_chat_agent(
            "test query", "test response", tmp_db, config,
        )
        assert actions == []

    @patch("giva.llm.engine.manager")
    def test_no_intents_returns_empty(self, mock_manager, tmp_db, config):
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "none"}], "topic": "chat", "progress": []}'
        )
        actions = run_post_chat_agent("hi", "hello!", tmp_db, config)
        assert actions == []

    @patch("giva.llm.engine.manager")
    def test_goal_scoped_auto_links(self, mock_manager, tmp_db, config):
        goal_id = tmp_db.add_goal(Goal(title="Health", tier="long_term"))
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "create_task", "title": "Jog 5k"}], '
            '"topic": "exercise", "progress": []}'
        )
        actions = run_post_chat_agent(
            "I should jog 5k", "That's a great plan!", tmp_db, config,
            goal_id=goal_id,
        )
        assert len(actions) == 1
        assert actions[0]["goal_id"] == goal_id

    @patch("giva.llm.engine.manager")
    def test_create_objective_triggers_enrichment_and_decomposition(
        self, mock_manager, tmp_db, config
    ):
        """When an objective is created, enrichment and decomposition run."""
        parent_id = tmp_db.add_goal(Goal(title="Investor Relations", tier="long_term"))

        # Three LLM calls: 1) intent detection, 2) enrichment, 3) decomposition
        mock_manager.generate.side_effect = [
            # 1. Intent detection → create_objective
            '{"intents": [{"type": "create_objective", "title": "Biweekly investor update",'
            ' "description": "Template for updates", "tier": "mid_term"}],'
            ' "topic": "investor", "progress": []}',
            # 2. Enrichment
            '{"enriched_description": "## Biweekly Investor Update Template\\n'
            'Draft template with KPIs, milestones, and team highlights."}',
            # 3. Decomposition
            '{"tasks": [{"title": "Draft KPI section of template",'
            ' "description": "Include revenue and growth metrics",'
            ' "priority": "high"},'
            ' {"title": "Schedule first biweekly send",'
            ' "description": null, "priority": "medium"}]}',
        ]

        actions = run_post_chat_agent(
            "Let's create a biweekly investor update template",
            "Great idea! Here's a draft template with KPIs, milestones...",
            tmp_db, config,
            goal_id=parent_id,
        )

        # Should have: objective_created + objective_enriched + 2x task_created
        types = [a["type"] for a in actions]
        assert "objective_created" in types
        assert "objective_enriched" in types
        assert types.count("task_created") == 2

        # Verify the enriched description was saved
        obj = actions[0]
        assert obj["type"] == "objective_created"
        goal = tmp_db.get_goal(obj["goal_id"])
        assert "KPI" in goal.description or "Investor Update" in goal.description

        # Verify tasks were linked to the new objective
        tasks = tmp_db.get_tasks(status="pending")
        task_titles = [t.title for t in tasks]
        assert "Draft KPI section of template" in task_titles
        assert "Schedule first biweekly send" in task_titles


class TestEnrichObjectiveDescription:

    @patch("giva.llm.engine.manager")
    def test_enriches_description(self, mock_manager, tmp_db, config):
        """Enrichment updates the goal's description with conversation context."""
        goal_id = tmp_db.add_goal(Goal(
            title="Launch newsletter",
            tier="mid_term",
            description="Brief desc",
        ))

        mock_manager.generate.return_value = (
            '{"enriched_description": "## Newsletter Launch Plan\\n'
            '- Weekly cadence, Mondays at 9am\\n'
            '- Template: intro + 3 stories + CTA\\n'
            '- Target: 500 subscribers by Q2"}'
        )

        action = _enrich_objective_description(
            goal_id,
            "I want to start a newsletter",
            "Great! Here's a plan: weekly on Mondays...",
            tmp_db, config,
        )

        assert action is not None
        assert action["type"] == "objective_enriched"
        assert action["goal_id"] == goal_id

        # Verify description updated in DB
        goal = tmp_db.get_goal(goal_id)
        assert "Newsletter Launch Plan" in goal.description
        assert "500 subscribers" in goal.description

    @patch("giva.llm.engine.manager")
    def test_enrichment_noop_on_empty_response(self, mock_manager, tmp_db, config):
        """If LLM returns empty enriched_description, no update happens."""
        goal_id = tmp_db.add_goal(Goal(
            title="Test goal", tier="short_term", description="Original",
        ))

        mock_manager.generate.return_value = '{"enriched_description": ""}'

        action = _enrich_objective_description(
            goal_id, "query", "response", tmp_db, config,
        )

        assert action is None
        goal = tmp_db.get_goal(goal_id)
        assert goal.description == "Original"

    @patch("giva.llm.engine.manager")
    def test_enrichment_handles_llm_error(self, mock_manager, tmp_db, config):
        """LLM error doesn't crash, returns None."""
        goal_id = tmp_db.add_goal(Goal(title="Test", tier="short_term"))
        mock_manager.generate.side_effect = RuntimeError("Model crashed")

        action = _enrich_objective_description(
            goal_id, "query", "response", tmp_db, config,
        )
        assert action is None

    def test_enrichment_nonexistent_goal(self, tmp_db, config):
        """Nonexistent goal returns None without LLM call."""
        action = _enrich_objective_description(
            9999, "query", "response", tmp_db, config,
        )
        assert action is None


class TestDecomposeObjectiveToTasks:

    @patch("giva.llm.engine.manager")
    def test_creates_tasks_from_conversation(self, mock_manager, tmp_db, config):
        """Decomposition creates tasks linked to the objective."""
        goal_id = tmp_db.add_goal(Goal(
            title="Website redesign",
            tier="mid_term",
            description="Redesign the company website",
        ))

        mock_manager.generate.return_value = (
            '{"tasks": ['
            '{"title": "Create wireframes for homepage", "description": "Low-fi mockups",'
            ' "priority": "high"},'
            '{"title": "Set up staging environment", "description": null,'
            ' "priority": "medium"},'
            '{"title": "Write copy for About page", "description": "Include team bios",'
            ' "priority": "low"}'
            ']}'
        )

        actions = _decompose_objective_to_tasks(
            goal_id, "Let's redesign the website",
            "I'll help! Here are the key steps...",
            tmp_db, config,
        )

        assert len(actions) == 3
        assert all(a["type"] == "task_created" for a in actions)
        assert all(a["goal_id"] == goal_id for a in actions)

        # Verify in DB
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 3
        titles = {t.title for t in tasks}
        assert "Create wireframes for homepage" in titles
        assert "Set up staging environment" in titles

    @patch("giva.llm.engine.manager")
    def test_skips_duplicate_tasks(self, mock_manager, tmp_db, config):
        """Existing tasks are not duplicated."""
        goal_id = tmp_db.add_goal(Goal(title="Project", tier="mid_term"))
        tmp_db.add_task(Task(
            title="Create wireframes", source_type="chat", source_id=0,
        ))

        mock_manager.generate.return_value = (
            '{"tasks": ['
            '{"title": "Create wireframes", "priority": "high"},'
            '{"title": "Write copy", "priority": "medium"}'
            ']}'
        )

        actions = _decompose_objective_to_tasks(
            goal_id, "q", "r", tmp_db, config,
        )

        # Only the non-duplicate should be created
        assert len(actions) == 1
        assert actions[0]["title"] == "Write copy"

    @patch("giva.llm.engine.manager")
    def test_empty_tasks_list(self, mock_manager, tmp_db, config):
        """LLM returning no tasks produces empty actions."""
        goal_id = tmp_db.add_goal(Goal(title="Vague goal", tier="mid_term"))
        mock_manager.generate.return_value = '{"tasks": []}'

        actions = _decompose_objective_to_tasks(
            goal_id, "q", "r", tmp_db, config,
        )
        assert actions == []

    @patch("giva.llm.engine.manager")
    def test_decomposition_handles_llm_error(self, mock_manager, tmp_db, config):
        """LLM error doesn't crash, returns empty list."""
        goal_id = tmp_db.add_goal(Goal(title="Test", tier="short_term"))
        mock_manager.generate.side_effect = RuntimeError("Model crashed")

        actions = _decompose_objective_to_tasks(
            goal_id, "q", "r", tmp_db, config,
        )
        assert actions == []

    def test_decomposition_nonexistent_goal(self, tmp_db, config):
        """Nonexistent goal returns empty list without LLM call."""
        actions = _decompose_objective_to_tasks(
            9999, "q", "r", tmp_db, config,
        )
        assert actions == []


class TestHandleCreateObjectiveWithEnrichment:

    @patch("giva.llm.engine.manager")
    def test_enrichment_runs_when_config_provided(self, mock_manager, tmp_db, config):
        """With config and full text, enrichment + decomposition run."""
        mock_manager.generate.side_effect = [
            # Enrichment
            '{"enriched_description": "Rich description with details"}',
            # Decomposition
            '{"tasks": [{"title": "Step 1", "priority": "medium"}]}',
        ]

        intent = {"title": "Build dashboard", "description": "Analytics dash"}
        action = _handle_create_objective(
            intent, tmp_db,
            full_query="I need an analytics dashboard for the team",
            full_response="Great! Let me outline the approach...",
            config=config,
        )

        assert action is not None
        assert action["type"] == "objective_created"

        # Verify enrichment ran
        goal = tmp_db.get_goal(action["goal_id"])
        assert "Rich description" in goal.description

        # Verify decomposition ran (task created)
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0].title == "Step 1"

    def test_no_enrichment_without_config(self, tmp_db):
        """Without config, objective is created but no enrichment/decomposition."""
        intent = {"title": "Simple objective", "description": "Brief"}
        action = _handle_create_objective(intent, tmp_db)

        assert action is not None
        assert action["type"] == "objective_created"
        assert action["extra_actions"] == []

        # No tasks created by decomposition
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 0

    @patch("giva.llm.engine.manager")
    def test_enrichment_failure_doesnt_block_decomposition(
        self, mock_manager, tmp_db, config
    ):
        """If enrichment fails, decomposition still runs."""
        mock_manager.generate.side_effect = [
            # Enrichment fails (bad JSON)
            "not valid json at all",
            # Decomposition succeeds
            '{"tasks": [{"title": "Task from decomp", "priority": "high"}]}',
        ]

        intent = {"title": "Resilient goal", "description": "Test"}
        action = _handle_create_objective(
            intent, tmp_db,
            full_query="query", full_response="response",
            config=config,
        )

        assert action is not None
        # Decomposition should have created a task even though enrichment failed
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0].title == "Task from decomp"
