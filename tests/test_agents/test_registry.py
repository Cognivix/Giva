"""Tests for the agent registry."""

from __future__ import annotations

import pytest

from giva.agents.base import AgentManifest, AgentResult, BaseAgent
from giva.agents.registry import AgentRegistry


class DummyAgent(BaseAgent):
    """A minimal agent for testing."""

    def __init__(self, agent_id="dummy"):
        super().__init__(AgentManifest(
            agent_id=agent_id,
            name="Dummy Agent",
            description="A test dummy agent.",
            examples=["do a dummy thing"],
        ))

    def execute(self, query, context, store, config):
        return AgentResult(success=True, output="dummy output")


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = DummyAgent()
        reg.register(agent)
        assert reg.get("dummy") is agent

    def test_get_missing(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_manifests(self):
        reg = AgentRegistry()
        reg.register(DummyAgent("a"))
        reg.register(DummyAgent("b"))
        manifests = reg.list_manifests()
        assert len(manifests) == 2
        ids = {m.agent_id for m in manifests}
        assert ids == {"a", "b"}

    def test_has_agents(self):
        reg = AgentRegistry()
        assert reg.has_agents() is False
        reg.register(DummyAgent())
        assert reg.has_agents() is True

    def test_catalog_text_empty(self):
        reg = AgentRegistry()
        text = reg.catalog_text()
        assert "No specialized agents" in text

    def test_catalog_text_with_agents(self):
        reg = AgentRegistry()
        reg.register(DummyAgent())
        text = reg.catalog_text()
        assert "dummy" in text
        assert "Dummy Agent" in text
        assert "do a dummy thing" in text

    def test_duplicate_registration_replaces(self):
        reg = AgentRegistry()
        a1 = DummyAgent("same_id")
        a2 = DummyAgent("same_id")
        reg.register(a1)
        reg.register(a2)
        assert reg.get("same_id") is a2
        assert len(reg.list_manifests()) == 1

    def test_register_non_agent_raises(self):
        reg = AgentRegistry()
        with pytest.raises(TypeError):
            reg.register("not an agent")

    def test_discover_finds_email_drafter(self):
        """The email_drafter package should be discovered."""
        reg = AgentRegistry()
        count = reg.discover()
        assert count >= 1
        assert reg.get("email_drafter") is not None

    def test_discover_with_schema(self, tmp_path):
        """Discover should call schema() on agents that have it."""
        import sqlite3

        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.close()

        reg = AgentRegistry()
        # Register an agent with a schema method
        agent = DummyAgent("schema_agent")
        agent.schema = lambda: "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY)"
        reg.register(agent)
        reg._run_schema(agent, db)

        # Verify the table was created
        conn = sqlite3.connect(str(db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1
