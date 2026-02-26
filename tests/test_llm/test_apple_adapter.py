"""Tests for the Apple Foundation Model adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from giva.llm.apple_adapter import (
    APPLE_MODEL_ID,
    AppleModelAdapter,
    check_apple_model_availability,
    is_apple_model,
)


# --- is_apple_model ---


def test_is_apple_model_true():
    assert is_apple_model("apple") is True


def test_is_apple_model_case_insensitive():
    assert is_apple_model("Apple") is True
    assert is_apple_model("APPLE") is True


def test_is_apple_model_false():
    assert is_apple_model("mlx-community/Qwen3-8B-4bit") is False
    assert is_apple_model("") is False
    assert is_apple_model("apple-pie") is False


def test_apple_model_id_sentinel():
    assert APPLE_MODEL_ID == "apple"


# --- check_apple_model_availability ---


def test_availability_sdk_not_installed():
    """When apple_fm_sdk is not importable, availability returns False."""
    # Patch sys.modules so 'import apple_fm_sdk' raises ImportError
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "apple_fm_sdk":
            raise ImportError("No module named 'apple_fm_sdk'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_blocked_import):
        available, reason = check_apple_model_availability()

    assert available is False
    assert "not installed" in reason


def test_availability_model_available():
    mock_fm = MagicMock()
    mock_model = MagicMock()
    mock_model.is_available.return_value = (True, None)
    mock_fm.SystemLanguageModel.return_value = mock_model

    with patch.dict("sys.modules", {"apple_fm_sdk": mock_fm}):
        available, reason = check_apple_model_availability()

    assert available is True
    assert reason is None


def test_availability_intelligence_not_enabled():
    mock_fm = MagicMock()
    mock_model = MagicMock()
    reason_enum = MagicMock()
    reason_enum.name = "APPLE_INTELLIGENCE_NOT_ENABLED"
    mock_model.is_available.return_value = (False, reason_enum)
    mock_fm.SystemLanguageModel.return_value = mock_model

    with patch.dict("sys.modules", {"apple_fm_sdk": mock_fm}):
        available, reason = check_apple_model_availability()

    assert available is False
    assert "Apple Intelligence is not enabled" in reason


def test_availability_device_not_eligible():
    mock_fm = MagicMock()
    mock_model = MagicMock()
    reason_enum = MagicMock()
    reason_enum.name = "DEVICE_NOT_ELIGIBLE"
    mock_model.is_available.return_value = (False, reason_enum)
    mock_fm.SystemLanguageModel.return_value = mock_model

    with patch.dict("sys.modules", {"apple_fm_sdk": mock_fm}):
        available, reason = check_apple_model_availability()

    assert available is False
    assert "does not support" in reason


def test_availability_model_not_ready():
    mock_fm = MagicMock()
    mock_model = MagicMock()
    reason_enum = MagicMock()
    reason_enum.name = "MODEL_NOT_READY"
    mock_model.is_available.return_value = (False, reason_enum)
    mock_fm.SystemLanguageModel.return_value = mock_model

    with patch.dict("sys.modules", {"apple_fm_sdk": mock_fm}):
        available, reason = check_apple_model_availability()

    assert available is False
    assert "still downloading" in reason


# --- AppleModelAdapter ---


class TestAppleModelAdapter:
    def test_messages_to_prompt_system_only(self):
        messages = [{"role": "system", "content": "You are a classifier."}]
        instructions, prompt = AppleModelAdapter._messages_to_prompt(messages)
        assert instructions == "You are a classifier."
        assert prompt == ""

    def test_messages_to_prompt_system_and_user(self):
        messages = [
            {"role": "system", "content": "You are a classifier."},
            {"role": "user", "content": "Classify this email."},
        ]
        instructions, prompt = AppleModelAdapter._messages_to_prompt(messages)
        assert instructions == "You are a classifier."
        assert prompt == "Classify this email."

    def test_messages_to_prompt_multi_turn(self):
        messages = [
            {"role": "system", "content": "System instructions."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow up"},
        ]
        instructions, prompt = AppleModelAdapter._messages_to_prompt(messages)
        assert instructions == "System instructions."
        assert "First question" in prompt
        assert "[Previous response]: First answer" in prompt
        assert "Follow up" in prompt

    def test_messages_to_prompt_multiple_system(self):
        messages = [
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "Hello"},
        ]
        instructions, prompt = AppleModelAdapter._messages_to_prompt(messages)
        assert "Rule 1" in instructions
        assert "Rule 2" in instructions
        assert prompt == "Hello"

    def test_messages_to_prompt_user_only(self):
        messages = [{"role": "user", "content": "Hello"}]
        instructions, prompt = AppleModelAdapter._messages_to_prompt(messages)
        assert instructions == ""
        assert prompt == "Hello"

    def test_messages_to_prompt_empty(self):
        instructions, prompt = AppleModelAdapter._messages_to_prompt([])
        assert instructions == ""
        assert prompt == ""

    def test_generate_calls_sdk(self):
        adapter = AppleModelAdapter()

        mock_fm = MagicMock()
        mock_session = MagicMock()
        mock_session.respond = AsyncMock(return_value="classification: KEEP")
        mock_fm.LanguageModelSession.return_value = mock_session

        with patch.dict("sys.modules", {"apple_fm_sdk": mock_fm}):
            adapter._sdk_available = None  # Reset cached check
            result = adapter.generate(
                messages=[
                    {"role": "system", "content": "Classify emails."},
                    {"role": "user", "content": "Subject: Meeting tomorrow"},
                ],
                max_tokens=256,
                temp=0.1,
            )

        assert result == "classification: KEEP"
        mock_fm.LanguageModelSession.assert_called_once()
        mock_session.respond.assert_called_once()

    def test_generate_sdk_not_installed(self):
        adapter = AppleModelAdapter()
        adapter._sdk_available = False

        with pytest.raises(RuntimeError, match="not installed"):
            adapter.generate(
                messages=[{"role": "user", "content": "test"}],
            )


# --- ModelManager integration ---


def test_model_manager_routes_apple_to_adapter():
    """Verify that ModelManager.generate routes 'apple' to the adapter."""
    from giva.llm.engine import ModelManager

    mgr = ModelManager()

    with patch("giva.llm.apple_adapter.adapter") as mock_adapter:
        mock_adapter.generate.return_value = '{"decision": "KEEP"}'
        result = mgr.generate(
            "apple",
            [{"role": "user", "content": "test"}],
            max_tokens=128,
        )

    assert result == '{"decision": "KEEP"}'
    mock_adapter.generate.assert_called_once()


def test_model_manager_is_loaded_apple():
    """Apple model is always 'loaded'."""
    from giva.llm.engine import ModelManager

    mgr = ModelManager()
    assert mgr.is_loaded("apple") is True


def test_model_manager_unload_apple_noop():
    """Unloading the Apple model is a no-op."""
    from giva.llm.engine import ModelManager

    mgr = ModelManager()
    mgr.unload("apple")  # Should not raise


def test_model_manager_stream_generate_apple():
    """Verify stream_generate falls back to single-shot for Apple model."""
    from giva.llm.engine import ModelManager

    mgr = ModelManager()

    with patch("giva.llm.apple_adapter.adapter") as mock_adapter:
        mock_adapter.generate.return_value = "streamed response"
        chunks = list(
            mgr.stream_generate(
                "apple",
                [{"role": "user", "content": "test"}],
            )
        )

    assert chunks == ["streamed response"]
