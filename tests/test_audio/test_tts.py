"""Tests for the TTS engine — sentence splitting and synthesis interface."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from giva.audio.tts import TTSEngine, split_sentences
from giva.config import VoiceConfig


# --- split_sentences tests ---


class TestSplitSentences:
    def test_single_sentence_no_terminator(self):
        """Incomplete text returns as single element."""
        assert split_sentences("Hello world") == ["Hello world"]

    def test_single_complete_sentence(self):
        """A sentence ending with period is one complete sentence."""
        result = split_sentences("Hello world.")
        assert result == ["Hello world."]

    def test_two_sentences(self):
        """Two sentences split correctly."""
        result = split_sentences("Hello world. How are you?")
        assert len(result) == 2
        assert result[0] == "Hello world."
        assert result[1] == "How are you?"

    def test_three_sentences(self):
        result = split_sentences("First. Second! Third?")
        assert len(result) == 3

    def test_sentence_with_incomplete_trailing(self):
        """Complete sentence followed by incomplete text."""
        result = split_sentences("Hello world. How are")
        assert len(result) == 2
        assert result[0] == "Hello world."
        assert result[1] == "How are"

    def test_exclamation_mark(self):
        result = split_sentences("Wow! That's great.")
        assert len(result) == 2

    def test_question_mark(self):
        result = split_sentences("What? Really.")
        assert len(result) == 2

    def test_empty_string(self):
        result = split_sentences("")
        assert result == [""]

    def test_multiple_spaces(self):
        result = split_sentences("First sentence.  Second sentence.")
        assert len(result) == 2


# --- TTSEngine tests (mocked) ---


class TestTTSEngine:
    def test_init(self):
        config = VoiceConfig()
        engine = TTSEngine(config)
        assert not engine.is_loaded()

    def test_unload_when_not_loaded(self):
        """Unloading when not loaded should not raise."""
        config = VoiceConfig()
        engine = TTSEngine(config)
        engine.unload()
        assert not engine.is_loaded()

    @patch("giva.audio.tts.TTSEngine._ensure_loaded")
    def test_synthesize_calls_model(self, mock_ensure):
        """synthesize() should call the model and return numpy array."""
        config = VoiceConfig()
        engine = TTSEngine(config)

        # Set up mock model
        mock_result = MagicMock()
        mock_result.audio = np.zeros(1000, dtype=np.float32)
        engine._model = MagicMock()
        engine._model.generate.return_value = [mock_result]

        with patch("mlx.core", create=True):
            audio, sr = engine.synthesize("Hello world")

        assert isinstance(audio, np.ndarray)
        assert sr == config.sample_rate
        engine._model.generate.assert_called_once()

    def test_synthesize_sentences_buffers_correctly(self):
        """synthesize_sentences() should yield audio for each complete sentence."""
        config = VoiceConfig()
        engine = TTSEngine(config)

        # Mock the synthesize method
        engine.synthesize = MagicMock(
            return_value=(np.zeros(100, dtype=np.float32), 24000)
        )

        # Simulate token stream: "Hello world. How are you?"
        tokens = ["Hello", " world", ".", " How", " are", " you", "?"]

        results = list(engine.synthesize_sentences(iter(tokens)))

        # Should produce 2 audio chunks (2 sentences)
        assert len(results) == 2
        assert engine.synthesize.call_count == 2
