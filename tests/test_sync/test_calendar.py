"""Tests for calendar sync via AppleScript (mocked JXA)."""

from datetime import datetime
from unittest.mock import patch, MagicMock

from giva.db.models import Event
from giva.sync.calendar import (
    _participant_status,
    _sync_applescript,
    sync_calendar,
)


class TestParticipantStatus:

    def test_known_statuses(self):
        assert _participant_status(0) == "unknown"
        assert _participant_status(1) == "pending"
        assert _participant_status(2) == "accepted"
        assert _participant_status(3) == "declined"
        assert _participant_status(4) == "tentative"

    def test_unknown_status(self):
        assert _participant_status(99) == "unknown"


class TestSyncCalendar:

    @patch("giva.sync.calendar._eventkit_authorized", return_value=False)
    @patch("giva.sync.calendar._sync_applescript")
    def test_uses_applescript_when_eventkit_not_authorized(
        self, mock_applescript, mock_authorized, tmp_db,
    ):
        mock_applescript.return_value = 5
        result = sync_calendar(tmp_db, past_days=7, future_days=30)
        assert result == 5
        mock_applescript.assert_called_once_with(tmp_db, 7, 30)

    @patch("giva.sync.calendar._eventkit_authorized", return_value=True)
    @patch("giva.sync.calendar._sync_eventkit")
    def test_uses_eventkit_when_authorized(
        self, mock_eventkit, mock_authorized, tmp_db,
    ):
        mock_eventkit.return_value = 10
        result = sync_calendar(tmp_db, past_days=7, future_days=30)
        assert result == 10
        mock_eventkit.assert_called_once_with(tmp_db, 7, 30)


class TestSyncApplescript:

    @patch("giva.utils.applescript.run_jxa_json")
    def test_syncs_events_from_jxa(self, mock_jxa, tmp_db):
        mock_jxa.return_value = [
            {
                "uid": "event-1",
                "calendar": "Work",
                "summary": "Team standup",
                "description": "Daily sync",
                "location": "Zoom",
                "start": "2026-03-01T09:00:00Z",
                "end": "2026-03-01T09:30:00Z",
                "allDay": False,
            },
            {
                "uid": "event-2",
                "calendar": "Personal",
                "summary": "Lunch",
                "description": "",
                "location": "Cafe",
                "start": "2026-03-01T12:00:00Z",
                "end": "2026-03-01T13:00:00Z",
                "allDay": False,
            },
        ]

        count = _sync_applescript(tmp_db, past_days=7, future_days=30)
        assert count == 2

        events = tmp_db.get_upcoming_events(days=60)
        assert len(events) >= 2

    @patch("giva.utils.applescript.run_jxa_json")
    def test_handles_jxa_error(self, mock_jxa, tmp_db):
        mock_jxa.side_effect = RuntimeError("JXA timeout")
        count = _sync_applescript(tmp_db, past_days=7, future_days=30)
        assert count == 0

    @patch("giva.utils.applescript.run_jxa_json")
    def test_handles_malformed_event(self, mock_jxa, tmp_db):
        mock_jxa.return_value = [
            {
                "uid": "good-event",
                "calendar": "Work",
                "summary": "Good event",
                "start": "2026-03-01T09:00:00Z",
                "end": "2026-03-01T10:00:00Z",
            },
            {
                "uid": "bad-event",
                "start": "not-a-date",  # Will fail parsing
            },
        ]
        count = _sync_applescript(tmp_db, past_days=7, future_days=30)
        # At least the good event should be synced
        assert count >= 1

    @patch("giva.utils.applescript.run_jxa_json")
    def test_upserts_on_duplicate_uid(self, mock_jxa, tmp_db):
        events_data = [{
            "uid": "event-1",
            "calendar": "Work",
            "summary": "Original title",
            "start": "2026-03-01T09:00:00Z",
            "end": "2026-03-01T10:00:00Z",
        }]
        mock_jxa.return_value = events_data
        _sync_applescript(tmp_db, 7, 30)

        # Update the title
        events_data[0]["summary"] = "Updated title"
        mock_jxa.return_value = events_data
        _sync_applescript(tmp_db, 7, 30)

        # Should still be one event
        events = tmp_db.get_upcoming_events(days=60)
        uids = [e.uid for e in events]
        assert uids.count("event-1") == 1

    @patch("giva.utils.applescript.run_jxa_json")
    def test_handles_missing_end_date(self, mock_jxa, tmp_db):
        mock_jxa.return_value = [{
            "uid": "no-end",
            "calendar": "Work",
            "summary": "All day event",
            "start": "2026-03-01T00:00:00Z",
            "end": "",
            "allDay": True,
        }]
        count = _sync_applescript(tmp_db, 7, 30)
        assert count == 1

    @patch("giva.utils.applescript.run_jxa_json")
    def test_empty_calendar_returns_zero(self, mock_jxa, tmp_db):
        mock_jxa.return_value = []
        count = _sync_applescript(tmp_db, 7, 30)
        assert count == 0
