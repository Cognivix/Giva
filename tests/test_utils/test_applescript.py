"""Tests for AppleScript/JXA runner helpers."""

import json
from unittest.mock import patch, MagicMock

import pytest

from giva.utils.applescript import (
    run_applescript,
    run_jxa,
    run_jxa_json,
    check_fda_access,
)


class TestRunApplescript:

    @patch("giva.utils.applescript.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
        result = run_applescript('display dialog "hi"')
        assert result == "hello"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["osascript", "-e", 'display dialog "hi"']

    @patch("giva.utils.applescript.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with pytest.raises(RuntimeError, match="AppleScript failed"):
            run_applescript("bad script")

    @patch("giva.utils.applescript.subprocess.run")
    def test_timeout_passed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_applescript("script", timeout=30)
        assert mock_run.call_args[1]["timeout"] == 30


class TestRunJxa:

    @patch("giva.utils.applescript.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="result\n", stderr="")
        result = run_jxa("var x = 1;")
        assert result == "result"
        args = mock_run.call_args[0][0]
        assert args == ["osascript", "-l", "JavaScript", "-e", "var x = 1;"]

    @patch("giva.utils.applescript.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="JS error")
        with pytest.raises(RuntimeError, match="JXA failed"):
            run_jxa("bad script")


class TestRunJxaJson:

    @patch("giva.utils.applescript.run_jxa")
    def test_parses_json_array(self, mock_jxa):
        mock_jxa.return_value = '[{"name": "Alice"}, {"name": "Bob"}]'
        result = run_jxa_json("some script")
        assert len(result) == 2
        assert result[0]["name"] == "Alice"

    @patch("giva.utils.applescript.run_jxa")
    def test_parses_json_object(self, mock_jxa):
        mock_jxa.return_value = '{"key": "value"}'
        result = run_jxa_json("some script")
        assert result == {"key": "value"}

    @patch("giva.utils.applescript.run_jxa")
    def test_empty_output_returns_empty_list(self, mock_jxa):
        mock_jxa.return_value = ""
        result = run_jxa_json("some script")
        assert result == []

    @patch("giva.utils.applescript.run_jxa")
    def test_invalid_json_raises(self, mock_jxa):
        mock_jxa.return_value = "not json"
        with pytest.raises(json.JSONDecodeError):
            run_jxa_json("some script")


class TestCheckFdaAccess:

    @patch("giva.utils.applescript.run_jxa")
    def test_returns_true_when_readable(self, mock_jxa):
        mock_jxa.return_value = "true"
        assert check_fda_access() is True

    @patch("giva.utils.applescript.run_jxa")
    def test_returns_false_when_not_readable(self, mock_jxa):
        mock_jxa.return_value = "false"
        assert check_fda_access() is False

    @patch("giva.utils.applescript.run_jxa")
    def test_returns_false_on_error(self, mock_jxa):
        mock_jxa.side_effect = RuntimeError("JXA failed")
        assert check_fda_access() is False
