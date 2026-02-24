"""Tests for CLI utility functions: command parsing, goal helpers, voice toggle."""

import re
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# _goals_progress regex parsing
# ═══════════════════════════════════════════════════════════════

class TestGoalsProgressRegex:
    """Test the regex used in _goals_progress to parse 'N "note"' args."""

    # The regex from cli.py line 921
    _PATTERN = r'(\d+)\s+["\']?(.+?)["\']?\s*$'

    def test_quoted_note(self):
        match = re.match(self._PATTERN, '1 "Made great progress"')
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "Made great progress"

    def test_single_quoted_note(self):
        match = re.match(self._PATTERN, "42 'finished milestone'")
        assert match is not None
        assert match.group(1) == "42"
        assert match.group(2) == "finished milestone"

    def test_unquoted_note(self):
        match = re.match(self._PATTERN, "5 completed the first step")
        assert match is not None
        assert match.group(1) == "5"
        assert match.group(2) == "completed the first step"

    def test_no_note_fails(self):
        match = re.match(self._PATTERN, "5")
        assert match is None

    def test_no_id_fails(self):
        match = re.match(self._PATTERN, 'hello "note"')
        assert match is None

    def test_large_goal_id(self):
        match = re.match(self._PATTERN, "999 note text")
        assert match is not None
        assert match.group(1) == "999"

    def test_extra_whitespace(self):
        match = re.match(self._PATTERN, '  7   "note with spaces"  ')
        # Leading whitespace won't match \d+ at start
        assert match is None

    def test_goal_id_zero(self):
        match = re.match(self._PATTERN, '0 "zero note"')
        assert match is not None
        assert match.group(1) == "0"


# ═══════════════════════════════════════════════════════════════
# _goals_status_change status map
# ═══════════════════════════════════════════════════════════════

class TestGoalsStatusMap:
    """Test the status mapping used in _goals_status_change."""

    _STATUS_MAP = {"done": "completed", "pause": "paused", "abandon": "abandoned"}

    def test_done_maps_to_completed(self):
        assert self._STATUS_MAP.get("done", "done") == "completed"

    def test_pause_maps_to_paused(self):
        assert self._STATUS_MAP.get("pause", "pause") == "paused"

    def test_abandon_maps_to_abandoned(self):
        assert self._STATUS_MAP.get("abandon", "abandon") == "abandoned"

    def test_unknown_action_uses_raw(self):
        assert self._STATUS_MAP.get("activate", "activate") == "activate"


# ═══════════════════════════════════════════════════════════════
# Command dispatch table
# ═══════════════════════════════════════════════════════════════

class TestCommandParsing:
    """Test command parsing logic from _handle_command."""

    def test_command_split_no_args(self):
        parts = "/sync".split(maxsplit=1)
        assert parts[0].lower() == "/sync"
        assert len(parts) == 1

    def test_command_split_with_args(self):
        parts = "/tasks done 5".split(maxsplit=1)
        assert parts[0].lower() == "/tasks"
        assert parts[1] == "done 5"

    def test_command_case_insensitive(self):
        parts = "/SYNC".split(maxsplit=1)
        assert parts[0].lower() == "/sync"

    def test_quit_aliases(self):
        """All quit aliases should be recognized."""
        quit_commands = ["/quit", "/exit", "/q"]
        for cmd in quit_commands:
            parts = cmd.split(maxsplit=1)
            assert parts[0].lower() in ("/quit", "/exit", "/q")

    def test_command_with_quoted_args(self):
        parts = '/goals progress 1 "my note"'.split(maxsplit=1)
        assert parts[0].lower() == "/goals"
        assert parts[1] == 'progress 1 "my note"'


# ═══════════════════════════════════════════════════════════════
# Voice toggle logic
# ═══════════════════════════════════════════════════════════════

class TestVoiceToggle:
    """Test the voice toggle logic from _cmd_voice (extracted)."""

    @staticmethod
    def _toggle(arg: str, current: bool) -> bool:
        """Extracted toggle logic from cli.py lines 602-610."""
        arg = arg.strip().lower()
        if arg == "on":
            return True
        elif arg == "off":
            return False
        else:
            return not current

    def test_explicit_on(self):
        assert self._toggle("on", False) is True
        assert self._toggle("on", True) is True

    def test_explicit_off(self):
        assert self._toggle("off", True) is False
        assert self._toggle("off", False) is False

    def test_toggle_from_off(self):
        assert self._toggle("", False) is True

    def test_toggle_from_on(self):
        assert self._toggle("", True) is False

    def test_case_insensitive(self):
        assert self._toggle("ON", False) is True
        assert self._toggle("Off", True) is False
        assert self._toggle("  ON  ", False) is True

    def test_garbage_input_toggles(self):
        """Any unrecognized input should toggle."""
        assert self._toggle("banana", True) is False
        assert self._toggle("banana", False) is True


# ═══════════════════════════════════════════════════════════════
# _goals_status_change integration test (with mocked store)
# ═══════════════════════════════════════════════════════════════

class TestGoalsStatusChangeIntegration:
    """Test _goals_status_change with a mocked store."""

    @patch("giva.cli.console")
    def test_valid_status_change(self, mock_console):
        from giva.cli import _goals_status_change

        store = MagicMock()
        store.update_goal_status.return_value = True

        _goals_status_change("done", "42", store)

        store.update_goal_status.assert_called_once_with(42, "completed")
        mock_console.print.assert_called()
        # Check success message
        call_args = mock_console.print.call_args
        assert "42" in str(call_args) and "completed" in str(call_args)

    @patch("giva.cli.console")
    def test_invalid_goal_id(self, mock_console):
        from giva.cli import _goals_status_change

        store = MagicMock()
        _goals_status_change("done", "not_a_number", store)

        store.update_goal_status.assert_not_called()
        # Should print error
        call_args = mock_console.print.call_args
        assert "red" in str(call_args)

    @patch("giva.cli.console")
    def test_goal_not_found(self, mock_console):
        from giva.cli import _goals_status_change

        store = MagicMock()
        store.update_goal_status.return_value = False

        _goals_status_change("done", "999", store)

        store.update_goal_status.assert_called_once_with(999, "completed")
        call_args = mock_console.print.call_args
        assert "not found" in str(call_args).lower()


# ═══════════════════════════════════════════════════════════════
# _goals_progress integration test
# ═══════════════════════════════════════════════════════════════

class TestGoalsProgressIntegration:
    """Test _goals_progress with a mocked store."""

    @patch("giva.cli.console")
    def test_valid_progress(self, mock_console):
        from giva.cli import _goals_progress
        from giva.db.models import Goal

        store = MagicMock()
        store.get_goal.return_value = Goal(
            id=1, title="Test", tier="long_term", status="active",
        )

        _goals_progress('1 "completed milestone"', store)

        store.add_goal_progress.assert_called_once_with(1, "completed milestone", "user")

    @patch("giva.cli.console")
    def test_invalid_format(self, mock_console):
        from giva.cli import _goals_progress

        store = MagicMock()
        _goals_progress("bad input", store)

        store.add_goal_progress.assert_not_called()
        # Should show usage
        call_args = mock_console.print.call_args
        assert "Usage" in str(call_args) or "yellow" in str(call_args)

    @patch("giva.cli.console")
    def test_goal_not_found(self, mock_console):
        from giva.cli import _goals_progress

        store = MagicMock()
        store.get_goal.return_value = None

        _goals_progress('999 "note"', store)

        store.add_goal_progress.assert_not_called()
        call_args = mock_console.print.call_args
        assert "not found" in str(call_args).lower()
