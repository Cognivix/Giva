"""Tests for data model safety: _safe_json_loads and from_row resilience."""

import json
from datetime import datetime

from giva.db.models import (
    Email,
    Event,
    GoalStrategy,
    UserProfile,
    _safe_json_loads,
)


# ═══════════════════════════════════════════════════════════════
# _safe_json_loads
# ═══════════════════════════════════════════════════════════════


class TestSafeJsonLoads:
    """Verify _safe_json_loads never crashes on bad input."""

    def test_valid_list(self):
        assert _safe_json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_valid_dict(self):
        assert _safe_json_loads('{"a": 1}') == {"a": 1}

    def test_valid_empty_list(self):
        assert _safe_json_loads('[]') == []

    def test_valid_empty_dict(self):
        assert _safe_json_loads('{}') == {}

    def test_malformed_json_returns_default_list(self):
        assert _safe_json_loads('{bad json}') == []

    def test_malformed_json_returns_custom_default(self):
        assert _safe_json_loads('{bad}', default={}) == {}

    def test_none_input_returns_default(self):
        assert _safe_json_loads(None) == []

    def test_empty_string_returns_default(self):
        """Empty string is invalid JSON → returns default."""
        assert _safe_json_loads('') == []

    def test_truncated_json_returns_default(self):
        assert _safe_json_loads('[1, 2,') == []

    def test_random_text_returns_default(self):
        assert _safe_json_loads('not json at all') == []

    def test_nested_valid_json(self):
        result = _safe_json_loads('[{"key": [1, 2]}]')
        assert result == [{"key": [1, 2]}]

    def test_number_is_valid_json(self):
        """A bare number is valid JSON."""
        assert _safe_json_loads('42') == 42

    def test_string_is_valid_json(self):
        assert _safe_json_loads('"hello"') == "hello"


# ═══════════════════════════════════════════════════════════════
# from_row resilience with corrupted JSON fields
# ═══════════════════════════════════════════════════════════════


class TestEmailFromRowCorrupted:
    """Email.from_row should not crash on corrupted JSON fields."""

    def _base_row(self, **overrides):
        row = {
            "id": 1,
            "message_id": "test@example.com",
            "folder": "INBOX",
            "from_addr": "alice@example.com",
            "from_name": "Alice",
            "to_addrs": "[]",
            "cc_addrs": "[]",
            "subject": "Test",
            "date_sent": "2026-01-01T00:00:00",
            "body_plain": "",
            "body_html": "",
            "has_attachments": 0,
            "attachment_names": "[]",
            "in_reply_to": "",
            "references_list": "[]",
            "is_read": 0,
            "is_flagged": 0,
        }
        row.update(overrides)
        return row

    def test_valid_row(self):
        email = Email.from_row(self._base_row())
        assert email.message_id == "test@example.com"

    def test_corrupted_to_addrs(self):
        email = Email.from_row(self._base_row(to_addrs="{bad json}"))
        assert email.to_addrs == []

    def test_corrupted_cc_addrs(self):
        email = Email.from_row(self._base_row(cc_addrs="not json"))
        assert email.cc_addrs == []

    def test_corrupted_attachment_names(self):
        email = Email.from_row(self._base_row(attachment_names="[broken"))
        assert email.attachment_names == []

    def test_corrupted_references_list(self):
        email = Email.from_row(self._base_row(references_list="{nope}"))
        assert email.references_list == []


class TestEventFromRowCorrupted:
    """Event.from_row should not crash on corrupted JSON."""

    def _base_row(self, **overrides):
        row = {
            "id": 1,
            "uid": "event-uid",
            "calendar_name": "Work",
            "summary": "Meeting",
            "description": "",
            "location": "",
            "dtstart": "2026-01-01T10:00:00",
            "dtend": "2026-01-01T11:00:00",
            "all_day": 0,
            "organizer": "",
            "attendees": "[]",
            "status": "CONFIRMED",
        }
        row.update(overrides)
        return row

    def test_corrupted_attendees(self):
        event = Event.from_row(self._base_row(attendees="bad json"))
        assert event.attendees == []


class TestUserProfileFromRowCorrupted:
    """UserProfile.from_row should not crash on corrupted JSON."""

    def _base_row(self, **overrides):
        row = {
            "display_name": "Alice",
            "email_address": "alice@example.com",
            "top_contacts": "[]",
            "top_topics": "[]",
            "active_hours": "{}",
            "avg_response_time_min": 5.0,
            "email_volume_daily": 20.0,
            "profile_data": "{}",
            "updated_at": None,
        }
        row.update(overrides)
        return row

    def test_corrupted_top_contacts(self):
        profile = UserProfile.from_row(self._base_row(top_contacts="nope"))
        assert profile.top_contacts == []

    def test_corrupted_top_topics(self):
        profile = UserProfile.from_row(self._base_row(top_topics="{bad}"))
        assert profile.top_topics == []

    def test_corrupted_active_hours(self):
        profile = UserProfile.from_row(self._base_row(active_hours="[bad]"))
        assert profile.active_hours == {}

    def test_corrupted_profile_data(self):
        profile = UserProfile.from_row(self._base_row(profile_data="corrupt"))
        assert profile.profile_data == {}


class TestGoalStrategyFromRowCorrupted:
    """GoalStrategy.from_row should not crash on corrupted JSON."""

    def _base_row(self, **overrides):
        row = {
            "id": 1,
            "goal_id": 1,
            "strategy_text": "Do X",
            "action_items": "[]",
            "suggested_objectives": "[]",
            "status": "proposed",
            "created_at": "2026-01-01T00:00:00",
        }
        row.update(overrides)
        return row

    def test_corrupted_action_items(self):
        strategy = GoalStrategy.from_row(self._base_row(action_items="{bad"))
        assert strategy.action_items == []

    def test_corrupted_suggested_objectives(self):
        strategy = GoalStrategy.from_row(
            self._base_row(suggested_objectives="not json")
        )
        assert strategy.suggested_objectives == []
