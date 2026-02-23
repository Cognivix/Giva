"""Tests for the Email Drafter agent."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from giva.agents.email_drafter.agent import EmailDrafterAgent
from giva.config import GivaConfig
from giva.db.models import Email, UserProfile


class TestEmailDrafterAgent:
    def test_manifest(self):
        agent = EmailDrafterAgent()
        m = agent.manifest
        assert m.agent_id == "email_drafter"
        assert m.model_tier == "assistant"
        assert m.requires_confirmation is True
        assert len(m.examples) > 0

    def test_build_sender_context_no_profile(self):
        agent = EmailDrafterAgent()
        store = MagicMock()
        store.get_profile.return_value = None
        ctx = agent._build_sender_context(store)
        assert "Unknown" in ctx

    def test_build_sender_context_with_profile(self):
        agent = EmailDrafterAgent()
        store = MagicMock()
        store.get_profile.return_value = UserProfile(
            display_name="Alice Smith",
            email_address="alice@example.com",
            profile_data={
                "job_title": "Engineer",
                "company": "Acme Corp",
            },
        )
        ctx = agent._build_sender_context(store)
        assert "Alice Smith" in ctx
        assert "alice@example.com" in ctx
        assert "Engineer" in ctx
        assert "Acme Corp" in ctx

    def test_build_thread_context_no_results(self):
        agent = EmailDrafterAgent()
        store = MagicMock()
        store.search_emails.return_value = []
        ctx = agent._build_thread_context("test query", store)
        assert "No relevant" in ctx

    def test_build_thread_context_with_emails(self):
        agent = EmailDrafterAgent()
        store = MagicMock()
        store.search_emails.return_value = [
            Email(
                message_id="<test1>",
                folder="INBOX",
                from_addr="bob@example.com",
                from_name="Bob",
                subject="Project deadline",
                date_sent=datetime(2025, 1, 15),
                body_plain="The deadline is Friday.",
            ),
        ]
        ctx = agent._build_thread_context("deadline", store)
        assert "Bob" in ctx
        assert "Project deadline" in ctx
        assert "deadline is Friday" in ctx

    @patch("giva.agents.email_drafter.agent.EmailDrafterAgent._llm_generate")
    def test_execute_success(self, mock_generate):
        mock_generate.return_value = (
            '{"to": "bob@example.com", "subject": "Re: Deadline", '
            '"body": "Hi Bob, thanks for the update.", '
            '"reply_to_message_id": null}'
        )

        agent = EmailDrafterAgent()
        store = MagicMock()
        store.get_profile.return_value = None
        store.search_emails.return_value = []
        config = GivaConfig()

        result = agent.execute("reply to Bob about the deadline", {}, store, config)
        assert result.success is True
        assert "bob@example.com" in result.output
        assert "Re: Deadline" in result.output
        assert len(result.actions) == 1
        assert result.actions[0]["type"] == "email_draft_created"
        assert result.artifacts["to"] == "bob@example.com"
        assert result.artifacts["subject"] == "Re: Deadline"

    @patch("giva.agents.email_drafter.agent.EmailDrafterAgent._llm_generate")
    def test_execute_fallback_on_bad_json(self, mock_generate):
        mock_generate.return_value = "Here is a draft:\n\nDear Bob,\nThanks for the update."

        agent = EmailDrafterAgent()
        store = MagicMock()
        store.get_profile.return_value = None
        store.search_emails.return_value = []
        config = GivaConfig()

        result = agent.execute("reply to Bob", {}, store, config)
        assert result.success is True
        assert "draft" in result.output.lower()
        assert "raw_draft" in result.artifacts
