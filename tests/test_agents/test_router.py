"""Tests for the agent router."""

from __future__ import annotations

from giva.agents.base import AgentManifest
from giva.agents.router import keyword_prefilter


class TestKeywordPrefilter:
    def _make_manifest(self, agent_id, name, description, examples=None):
        return AgentManifest(
            agent_id=agent_id,
            name=name,
            description=description,
            examples=examples or [],
        )

    def test_matches_description_words(self):
        manifests = [
            self._make_manifest(
                "email_drafter", "Email Drafter",
                "Drafts professional emails based on instructions.",
                ["Draft a reply to Sarah's email"],
            ),
        ]
        candidates = keyword_prefilter("draft an email to John", manifests)
        assert len(candidates) == 1
        assert candidates[0].agent_id == "email_drafter"

    def test_no_match_unrelated_query(self):
        manifests = [
            self._make_manifest(
                "email_drafter", "Email Drafter",
                "Drafts professional emails.",
                ["Draft a reply"],
            ),
        ]
        candidates = keyword_prefilter("what is the weather today", manifests)
        assert len(candidates) == 0

    def test_matches_example_words(self):
        manifests = [
            self._make_manifest(
                "safari_agent", "Safari Automation",
                "Automates Safari browser for web tasks.",
                ["Open Safari and search for flights"],
            ),
        ]
        candidates = keyword_prefilter("search for flights to Paris", manifests)
        assert len(candidates) == 1

    def test_stop_words_ignored(self):
        manifests = [
            self._make_manifest(
                "file_agent", "File Manager",
                "Creates and manages files on disk.",
                ["Create a new spreadsheet"],
            ),
        ]
        # Query with only stop words + "the" shouldn't match
        candidates = keyword_prefilter("the and for with", manifests)
        assert len(candidates) == 0

    def test_multiple_agents_can_match(self):
        manifests = [
            self._make_manifest(
                "email_drafter", "Email Drafter",
                "Drafts emails.",
                ["Draft email"],
            ),
            self._make_manifest(
                "email_sender", "Email Sender",
                "Sends emails via Apple Mail.",
                ["Send email"],
            ),
        ]
        candidates = keyword_prefilter("send an email draft", manifests)
        assert len(candidates) == 2

    def test_short_words_ignored(self):
        """Words with <=2 characters should be ignored."""
        manifests = [
            self._make_manifest(
                "test", "AB CD",
                "An XY test.",
                ["Do AB"],
            ),
        ]
        # "AB" and "CD" are <= 2 chars, should be ignored
        candidates = keyword_prefilter("AB CD", manifests)
        assert len(candidates) == 0

    def test_empty_query(self):
        manifests = [
            self._make_manifest("x", "X", "something"),
        ]
        candidates = keyword_prefilter("", manifests)
        assert len(candidates) == 0

    def test_empty_manifests(self):
        candidates = keyword_prefilter("draft an email", [])
        assert len(candidates) == 0

    def test_case_insensitive(self):
        manifests = [
            self._make_manifest(
                "email_drafter", "Email Drafter",
                "Drafts professional EMAILS.",
            ),
        ]
        candidates = keyword_prefilter("DRAFT an EMAIL", manifests)
        assert len(candidates) == 1
