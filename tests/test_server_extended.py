"""Extended server tests: _ThinkParser edge cases, Pydantic models, _goal_to_response, CORS."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime

from giva.server import _ThinkParser, app


# ═══════════════════════════════════════════════════════════════
# _ThinkParser — Edge cases not covered in test_server.py
# ═══════════════════════════════════════════════════════════════

class TestThinkParserStartInThink:
    """Parser initialized with start_in_think=True (Qwen3-Thinking models)."""

    def test_starts_in_think_emits_thinking(self):
        p = _ThinkParser(start_in_think=True)
        events = p.feed("reasoning here</think>Answer")
        thinking = [e for e in events if e[0] == "thinking"]
        tokens = [e for e in events if e[0] == "token"]
        assert any("reasoning" in e[1] for e in thinking)
        assert any("Answer" in e[1] for e in tokens)

    def test_starts_in_think_no_close_tag(self):
        """All content is thinking when no close tag is ever sent."""
        p = _ThinkParser(start_in_think=True)
        events = p.feed("just reasoning")
        events += p.flush()
        assert all(e[0] == "thinking" for e in events)
        text = "".join(e[1] for e in events)
        assert "just reasoning" in text

    def test_starts_in_think_then_normal_response(self):
        p = _ThinkParser(start_in_think=True)
        events = p.feed("step 1\nstep 2</think>Final answer")
        thinking = "".join(e[1] for e in events if e[0] == "thinking")
        tokens = "".join(e[1] for e in events if e[0] == "token")
        assert "step 1" in thinking
        assert "step 2" in thinking
        assert "Final answer" in tokens


class TestThinkParserPartialTag:
    """Test _partial_tag_len static method."""

    def test_single_char_partial(self):
        assert _ThinkParser._partial_tag_len("<", "<think>") == 1

    def test_multi_char_partial(self):
        assert _ThinkParser._partial_tag_len("text<th", "<think>") == 3

    def test_full_tag_not_partial(self):
        # Full tag should NOT match as partial
        assert _ThinkParser._partial_tag_len("<think>", "<think>") == 0

    def test_no_match(self):
        assert _ThinkParser._partial_tag_len("xyz", "<think>") == 0

    def test_empty_text(self):
        assert _ThinkParser._partial_tag_len("", "<think>") == 0

    def test_close_tag_partial(self):
        assert _ThinkParser._partial_tag_len("</thi", "</think>") == 5


class TestThinkParserMultipleBlocks:
    """Multiple <think> blocks in one stream."""

    def test_two_think_blocks(self):
        p = _ThinkParser()
        events = p.feed("<think>A</think>mid<think>B</think>end")
        thinking = [e[1] for e in events if e[0] == "thinking"]
        tokens = [e[1] for e in events if e[0] == "token"]
        assert "A" in thinking
        assert "B" in thinking
        assert "mid" in tokens
        assert "end" in tokens

    def test_adjacent_think_blocks(self):
        p = _ThinkParser()
        events = p.feed("<think>first</think><think>second</think>answer")
        thinking = [e[1] for e in events if e[0] == "thinking"]
        tokens = [e[1] for e in events if e[0] == "token"]
        assert "first" in thinking
        assert "second" in thinking
        assert "answer" in tokens


class TestThinkParserWhitespace:
    """Whitespace handling around </think>."""

    def test_strips_newlines_after_close(self):
        p = _ThinkParser()
        events = p.feed("<think>r</think>\n\n\nAnswer")
        tokens = "".join(e[1] for e in events if e[0] == "token")
        assert tokens == "Answer"

    def test_preserves_spaces_after_close(self):
        """Spaces (not newlines) are preserved."""
        p = _ThinkParser()
        events = p.feed("<think>r</think>  Answer")
        tokens = "".join(e[1] for e in events if e[0] == "token")
        assert tokens == "  Answer"


# ═══════════════════════════════════════════════════════════════
# Pydantic model validation
# ═══════════════════════════════════════════════════════════════

class TestChatRequestValidation:
    def test_valid_query(self):
        from giva.server import ChatRequest
        req = ChatRequest(query="Hello")
        assert req.query == "Hello"
        assert req.voice is False

    def test_voice_flag(self):
        from giva.server import ChatRequest
        req = ChatRequest(query="Hi", voice=True)
        assert req.voice is True

    def test_rejects_empty_query(self):
        from pydantic import ValidationError
        from giva.server import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(query="")

    def test_rejects_too_long_query(self):
        from pydantic import ValidationError
        from giva.server import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(query="x" * 4097)

    def test_max_length_query_accepted(self):
        from giva.server import ChatRequest
        req = ChatRequest(query="x" * 4096)
        assert len(req.query) == 4096


class TestUpdateStatusRequestValidation:
    def test_valid_statuses(self):
        from giva.server import UpdateStatusRequest
        for s in ("pending", "in_progress", "done", "dismissed"):
            req = UpdateStatusRequest(status=s)
            assert req.status == s

    def test_rejects_invalid_status(self):
        from pydantic import ValidationError
        from giva.server import UpdateStatusRequest
        with pytest.raises(ValidationError):
            UpdateStatusRequest(status="completed")

    def test_rejects_empty_status(self):
        from pydantic import ValidationError
        from giva.server import UpdateStatusRequest
        with pytest.raises(ValidationError):
            UpdateStatusRequest(status="")


class TestTaskCreateRequestValidation:
    def test_defaults(self):
        from giva.server import TaskCreateRequest
        req = TaskCreateRequest(title="Buy milk")
        assert req.title == "Buy milk"
        assert req.description == ""
        assert req.priority == "medium"
        assert req.due_date is None
        assert req.goal_id is None

    def test_custom_priority(self):
        from giva.server import TaskCreateRequest
        req = TaskCreateRequest(title="Test", priority="high")
        assert req.priority == "high"

    def test_rejects_empty_title(self):
        from pydantic import ValidationError
        from giva.server import TaskCreateRequest
        with pytest.raises(ValidationError):
            TaskCreateRequest(title="")

    def test_rejects_long_title(self):
        from pydantic import ValidationError
        from giva.server import TaskCreateRequest
        with pytest.raises(ValidationError):
            TaskCreateRequest(title="x" * 201)

    def test_rejects_invalid_priority(self):
        from pydantic import ValidationError
        from giva.server import TaskCreateRequest
        with pytest.raises(ValidationError):
            TaskCreateRequest(title="Test", priority="urgent")

    def test_with_goal_id(self):
        from giva.server import TaskCreateRequest
        req = TaskCreateRequest(title="Test", goal_id=42)
        assert req.goal_id == 42


class TestTaskUpdateRequestValidation:
    def test_all_fields_optional(self):
        from giva.server import TaskUpdateRequest
        req = TaskUpdateRequest()
        assert req.title is None
        assert req.priority is None
        assert req.status is None

    def test_valid_status_update(self):
        from giva.server import TaskUpdateRequest
        req = TaskUpdateRequest(status="done")
        assert req.status == "done"

    def test_rejects_invalid_status(self):
        from pydantic import ValidationError
        from giva.server import TaskUpdateRequest
        with pytest.raises(ValidationError):
            TaskUpdateRequest(status="invalid")

    def test_rejects_invalid_priority(self):
        from pydantic import ValidationError
        from giva.server import TaskUpdateRequest
        with pytest.raises(ValidationError):
            TaskUpdateRequest(priority="critical")


class TestGoalRequestValidation:
    def test_basic_goal(self):
        from giva.server import GoalRequest
        req = GoalRequest(title="Learn Rust", tier="long_term")
        assert req.title == "Learn Rust"
        assert req.tier == "long_term"
        assert req.priority == "medium"

    def test_goal_with_all_fields(self):
        from giva.server import GoalRequest
        req = GoalRequest(
            title="Ship v2", tier="mid_term",
            description="Release version 2", category="work",
            priority="high", target_date="2026-12-31",
        )
        assert req.description == "Release version 2"
        assert req.priority == "high"


# ═══════════════════════════════════════════════════════════════
# _goal_to_response helper
# ═══════════════════════════════════════════════════════════════

class TestGoalToResponse:
    """Test the _goal_to_response DTO conversion function."""

    def _make_goal(self, **kwargs):
        from giva.db.models import Goal
        defaults = dict(
            id=1, title="Test Goal", tier="long_term",
            description="Desc", category="personal",
            parent_id=None, status="active", priority="high",
            target_date=None, created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 2),
        )
        defaults.update(kwargs)
        return Goal(**defaults)

    def test_basic_conversion(self):
        from giva.server import _goal_to_response

        store = MagicMock()
        store.get_goal_progress.return_value = []

        goal = self._make_goal()
        resp = _goal_to_response(goal, store)

        assert resp.id == 1
        assert resp.title == "Test Goal"
        assert resp.tier == "long_term"
        assert resp.status == "active"
        assert resp.priority == "high"
        assert resp.progress == []
        assert resp.children == []
        assert resp.strategies == []
        assert resp.tasks == []

    def test_includes_progress_entries(self):
        from giva.server import _goal_to_response
        from giva.db.models import GoalProgress

        progress = GoalProgress(
            id=10, goal_id=1, note="Made progress",
            source="user", created_at=datetime(2026, 2, 1),
        )
        store = MagicMock()
        store.get_goal_progress.return_value = [progress]

        resp = _goal_to_response(self._make_goal(), store)

        assert len(resp.progress) == 1
        assert resp.progress[0].note == "Made progress"
        assert resp.progress[0].source == "user"

    def test_include_detail_fetches_children(self):
        from giva.server import _goal_to_response
        from giva.db.models import Goal

        child = Goal(
            id=2, title="Child Goal", tier="mid_term",
            status="active", priority="medium",
        )
        store = MagicMock()
        store.get_goal_progress.return_value = []
        store.get_child_goals.return_value = [child]
        store.get_strategies.return_value = []
        store.get_tasks_for_goal.return_value = []

        resp = _goal_to_response(self._make_goal(), store, include_detail=True)

        assert len(resp.children) == 1
        assert resp.children[0]["title"] == "Child Goal"
        assert resp.children[0]["tier"] == "mid_term"

    def test_include_detail_fetches_strategies(self):
        from giva.server import _goal_to_response
        from giva.db.models import GoalStrategy

        strategy = GoalStrategy(
            id=3, goal_id=1, strategy_text="Do X then Y",
            action_items=[{"step": "1"}], status="proposed",
            suggested_objectives=[], created_at=datetime(2026, 1, 5),
        )
        store = MagicMock()
        store.get_goal_progress.return_value = []
        store.get_child_goals.return_value = []
        store.get_strategies.return_value = [strategy]
        store.get_tasks_for_goal.return_value = []

        resp = _goal_to_response(self._make_goal(), store, include_detail=True)

        assert len(resp.strategies) == 1
        assert resp.strategies[0]["strategy_text"] == "Do X then Y"

    def test_include_detail_fetches_tasks(self):
        from giva.server import _goal_to_response
        from giva.db.models import Task

        task = Task(
            id=4, title="Task 1", description="",
            source_type="chat", source_id=0,
            priority="high", status="pending",
            due_date=datetime(2026, 3, 1), goal_id=1,
        )
        store = MagicMock()
        store.get_goal_progress.return_value = []
        store.get_child_goals.return_value = []
        store.get_strategies.return_value = []
        store.get_tasks_for_goal.return_value = [task]

        resp = _goal_to_response(self._make_goal(), store, include_detail=True)

        assert len(resp.tasks) == 1
        assert resp.tasks[0]["title"] == "Task 1"

    def test_without_detail_skips_heavy_queries(self):
        from giva.server import _goal_to_response

        store = MagicMock()
        store.get_goal_progress.return_value = []

        resp = _goal_to_response(self._make_goal(), store, include_detail=False)

        assert resp.children == []
        store.get_child_goals.assert_not_called()
        store.get_strategies.assert_not_called()
        store.get_tasks_for_goal.assert_not_called()

    def test_date_serialization(self):
        from giva.server import _goal_to_response

        store = MagicMock()
        store.get_goal_progress.return_value = []

        goal = self._make_goal(
            target_date=datetime(2026, 6, 15),
            created_at=datetime(2026, 1, 1, 10, 30),
            updated_at=datetime(2026, 1, 2, 14, 0),
        )
        resp = _goal_to_response(goal, store)

        assert resp.target_date is not None
        assert "2026" in resp.target_date
        assert resp.created_at is not None
        assert resp.updated_at is not None

    def test_none_dates(self):
        from giva.server import _goal_to_response

        store = MagicMock()
        store.get_goal_progress.return_value = []

        goal = self._make_goal(
            target_date=None, created_at=None, updated_at=None,
        )
        resp = _goal_to_response(goal, store)

        assert resp.target_date is None
        assert resp.created_at is None
        assert resp.updated_at is None


# ═══════════════════════════════════════════════════════════════
# CORS configuration
# ═══════════════════════════════════════════════════════════════

class TestCORSConfiguration:
    """Verify CORS is restricted to localhost origins, not wildcard."""

    def test_no_wildcard_origin(self):
        """CORS must not allow '*' (wildcard) origins."""
        from starlette.middleware.cors import CORSMiddleware

        cors_middleware = None
        for mw in app.user_middleware:
            if mw.cls is CORSMiddleware:
                cors_middleware = mw
                break

        assert cors_middleware is not None, "CORSMiddleware not found on app"
        origins = cors_middleware.kwargs.get("allow_origins", [])
        assert "*" not in origins, "CORS should not allow wildcard origins"

    def test_localhost_origins_allowed(self):
        """CORS should allow localhost origins for the native app."""
        from starlette.middleware.cors import CORSMiddleware

        cors_middleware = None
        for mw in app.user_middleware:
            if mw.cls is CORSMiddleware:
                cors_middleware = mw
                break

        origins = cors_middleware.kwargs.get("allow_origins", [])
        assert any("127.0.0.1" in o for o in origins), \
            "CORS should allow 127.0.0.1"
        assert any("localhost" in o for o in origins), \
            "CORS should allow localhost"


# ═══════════════════════════════════════════════════════════════
# Whisper Hallucination Filter
# ═══════════════════════════════════════════════════════════════

from giva.server import _filter_hallucination, _WHISPER_HALLUCINATION_PATTERNS


class TestHallucinationFilter:
    """Verify _filter_hallucination catches known Whisper silence artifacts."""

    @pytest.mark.parametrize("text", [
        "Thank you.",
        "thank you",
        "THANK YOU",
        "Thanks for watching!",
        "thanks for watching.",
        "Bye",
        "bye bye.",
        "Goodbye.",
        "Hey.",
        "So.",
        "The end.",
        "You're going to be here.",
        "...",
        "",
        "  thank you  ",  # with whitespace
        "\n Thank you. \n",  # with newlines
    ])
    def test_known_hallucinations_filtered(self, text):
        assert _filter_hallucination(text) == ""

    @pytest.mark.parametrize("text", [
        "Schedule a meeting with Sarah tomorrow",
        "What's on my calendar today?",
        "Thank you for the email about the project",
        "Hey Sarah, can you send me the report?",
        "So I was thinking about the new feature",
        "The endpoint should return a 200 status",
    ])
    def test_real_speech_not_filtered(self, text):
        result = _filter_hallucination(text)
        assert result == text.strip()

    def test_hallucination_patterns_set_not_empty(self):
        assert len(_WHISPER_HALLUCINATION_PATTERNS) > 10

    def test_filter_returns_stripped_text(self):
        """Real speech should be stripped but otherwise unchanged."""
        assert _filter_hallucination("  hello world  ") == "hello world"

    def test_empty_string_in_patterns(self):
        """Empty string is a hallucination pattern (Whisper sometimes returns nothing)."""
        assert "" in _WHISPER_HALLUCINATION_PATTERNS
        assert _filter_hallucination("") == ""
        assert _filter_hallucination("   ") == ""


# ═══════════════════════════════════════════════════════════════
# POST /api/transcribe/stream — SSE streaming transcription
# ═══════════════════════════════════════════════════════════════

import json
from unittest.mock import patch

from starlette.testclient import TestClient


def _make_wav_bytes():
    """Minimal valid WAV file bytes (44-byte header + 160 zero samples)."""
    import struct

    sample_rate = 16000
    num_samples = 160
    data_size = num_samples * 2  # 16-bit samples

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,        # chunk size
        1,         # PCM format
        1,         # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,         # block align
        16,        # bits per sample
        b"data",
        data_size,
    )
    return header + b"\x00" * data_size


def _parse_sse_events(text: str) -> list[tuple[str, str]]:
    """Parse SSE event text into list of (event_type, data) tuples."""
    events = []
    current_event = ""
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            events.append((current_event, line[6:].strip()))
        elif line == "data:":
            events.append((current_event, ""))
    return events


class TestStreamTranscribeEndpoint:
    """Verify the /api/transcribe/stream SSE endpoint."""

    def test_returns_sse_events(self):
        """Endpoint should return partial → final → done SSE events."""
        mock_text = "Hello world"
        wav_bytes = _make_wav_bytes()

        mock_stt = MagicMock()
        mock_stt.transcribe_file.return_value = mock_text

        with patch("giva.server._get_stt_engine", return_value=mock_stt):
            config = MagicMock()
            config.voice.stt_model = "distil-medium.en"
            config.voice.enabled = True
            app.state.config = config

            client = TestClient(app)
            files = {"file": ("test.wav", wav_bytes, "audio/wav")}
            response = client.post(
                "/api/transcribe/stream",
                files=files,
                headers={"Accept": "text/event-stream", "X-Chunk-Id": "42"},
            )

            assert response.status_code == 200

            events = _parse_sse_events(response.text)
            event_types = [e[0] for e in events]
            assert "partial" in event_types
            assert "final" in event_types
            assert "done" in event_types

            # Check final event contains the text and chunk_id
            final_events = [e for e in events if e[0] == "final"]
            assert len(final_events) == 1
            final_data = json.loads(final_events[0][1])
            assert final_data["text"] == mock_text
            assert final_data["chunk_id"] == "42"

    def test_hallucination_filtered_in_stream(self):
        """Hallucinated text should be filtered to empty string in SSE events."""
        wav_bytes = _make_wav_bytes()

        mock_stt = MagicMock()
        mock_stt.transcribe_file.return_value = "Thank you."

        with patch("giva.server._get_stt_engine", return_value=mock_stt):
            config = MagicMock()
            config.voice.stt_model = "distil-medium.en"
            config.voice.enabled = True
            app.state.config = config

            client = TestClient(app)
            files = {"file": ("test.wav", wav_bytes, "audio/wav")}
            response = client.post(
                "/api/transcribe/stream",
                files=files,
                headers={"Accept": "text/event-stream"},
            )

            assert response.status_code == 200

            events = _parse_sse_events(response.text)
            final_events = [e for e in events if e[0] == "final"]
            assert len(final_events) == 1
            final_data = json.loads(final_events[0][1])
            assert final_data["text"] == ""  # Hallucination filtered out

    def test_empty_file_returns_400(self):
        """Empty audio file should return 400 error."""
        config = MagicMock()
        config.voice.enabled = True
        app.state.config = config

        client = TestClient(app)
        files = {"file": ("test.wav", b"", "audio/wav")}
        response = client.post("/api/transcribe/stream", files=files)
        assert response.status_code == 400

    def test_default_chunk_id(self):
        """Missing X-Chunk-Id header should default to '0'."""
        wav_bytes = _make_wav_bytes()

        mock_stt = MagicMock()
        mock_stt.transcribe_file.return_value = "test"

        with patch("giva.server._get_stt_engine", return_value=mock_stt):
            config = MagicMock()
            config.voice.stt_model = "distil-medium.en"
            config.voice.enabled = True
            app.state.config = config

            client = TestClient(app)
            files = {"file": ("test.wav", wav_bytes, "audio/wav")}
            response = client.post(
                "/api/transcribe/stream",
                files=files,
                headers={"Accept": "text/event-stream"},
            )

            assert response.status_code == 200
            events = _parse_sse_events(response.text)
            final_events = [e for e in events if e[0] == "final"]
            assert len(final_events) == 1
            final_data = json.loads(final_events[0][1])
            assert final_data["chunk_id"] == "0"
