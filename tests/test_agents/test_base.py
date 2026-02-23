"""Tests for the agent base abstractions."""

from __future__ import annotations

from giva.agents.base import AgentManifest, AgentResult, BaseAgent


class TestAgentManifest:
    def test_creation(self):
        m = AgentManifest(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent.",
        )
        assert m.agent_id == "test_agent"
        assert m.name == "Test Agent"
        assert m.model_tier == "filter"
        assert m.supports_streaming is False
        assert m.requires_confirmation is False
        assert m.examples == []

    def test_frozen(self):
        m = AgentManifest(agent_id="x", name="X", description="X")
        try:
            m.agent_id = "y"
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_with_all_fields(self):
        m = AgentManifest(
            agent_id="full",
            name="Full Agent",
            description="Everything set.",
            examples=["do thing", "do other thing"],
            model_tier="assistant",
            supports_streaming=True,
            requires_confirmation=True,
            version="1.0.0",
        )
        assert m.model_tier == "assistant"
        assert m.supports_streaming is True
        assert len(m.examples) == 2
        assert m.version == "1.0.0"


class TestAgentResult:
    def test_creation(self):
        r = AgentResult(success=True, output="hello")
        assert r.success is True
        assert r.output == "hello"
        assert r.actions == []
        assert r.artifacts == {}
        assert r.error is None

    def test_frozen(self):
        r = AgentResult(success=True, output="hi")
        try:
            r.success = False
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_with_actions_and_artifacts(self):
        r = AgentResult(
            success=True,
            output="done",
            actions=[{"type": "task_created", "task_id": 1}],
            artifacts={"file": "/tmp/test.txt"},
        )
        assert len(r.actions) == 1
        assert r.artifacts["file"] == "/tmp/test.txt"

    def test_error_result(self):
        r = AgentResult(success=False, output="", error="something broke")
        assert r.success is False
        assert r.error == "something broke"


class TestBaseAgent:
    def test_parse_json_safe_direct(self):
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        result = agent._parse_json_safe('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_safe_markdown(self):
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        raw = 'Here is the result:\n```json\n{"key": "value"}\n```'
        result = agent._parse_json_safe(raw)
        assert result == {"key": "value"}

    def test_parse_json_safe_with_think_tags(self):
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        raw = '<think>reasoning here</think>{"key": "value"}'
        result = agent._parse_json_safe(raw)
        assert result == {"key": "value"}

    def test_parse_json_safe_embedded(self):
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        raw = 'Some text before {"key": "value"} and after'
        result = agent._parse_json_safe(raw)
        assert result == {"key": "value"}

    def test_parse_json_safe_failure(self):
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        result = agent._parse_json_safe("not json at all")
        assert result is None

    def test_parse_json_safe_array_returns_none(self):
        """_parse_json_safe should only return dicts, not arrays."""
        agent = BaseAgent(AgentManifest(
            agent_id="test", name="Test", description="Test"
        ))
        result = agent._parse_json_safe('[1, 2, 3]')
        assert result is None

    def test_manifest_property(self):
        m = AgentManifest(agent_id="test", name="Test", description="Test")
        agent = BaseAgent(m)
        assert agent.manifest is m
        assert agent.manifest.agent_id == "test"

    def test_data_dir(self, tmp_path):
        from giva.config import GivaConfig

        config = GivaConfig(data_dir=tmp_path)
        agent = BaseAgent(AgentManifest(
            agent_id="my_agent", name="My Agent", description="Test"
        ))
        d = agent._data_dir(config)
        assert d == tmp_path / "agents" / "my_agent"
        assert d.exists()
