"""Tests for the STT engine — transcription interface."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from giva.audio.stt import STTEngine
from giva.config import VoiceConfig


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


_has_soundfile = _try_import("soundfile")
_has_sounddevice = _try_import("sounddevice")


class TestSTTEngine:
    def test_init(self):
        config = VoiceConfig()
        engine = STTEngine(config)
        assert not engine.is_loaded()

    def test_unload_when_not_loaded(self):
        config = VoiceConfig()
        engine = STTEngine(config)
        engine.unload()
        assert not engine.is_loaded()

    @patch("giva.audio.stt.STTEngine._ensure_loaded")
    def test_transcribe_file(self, mock_ensure):
        """transcribe_file() should call whisper and return text."""
        config = VoiceConfig()
        engine = STTEngine(config)
        engine._whisper = MagicMock()
        engine._whisper.transcribe.return_value = {"text": " Hello world "}

        result = engine.transcribe_file("/fake/audio.wav")
        assert result == "Hello world"
        engine._whisper.transcribe.assert_called_once_with("/fake/audio.wav")

    @patch("giva.audio.stt.STTEngine._ensure_loaded")
    def test_transcribe_file_empty(self, mock_ensure):
        """transcribe_file() should return empty string for empty results."""
        config = VoiceConfig()
        engine = STTEngine(config)
        engine._whisper = MagicMock()
        engine._whisper.transcribe.return_value = {"text": ""}

        result = engine.transcribe_file("/fake/audio.wav")
        assert result == ""

    @pytest.mark.skipif(not _has_soundfile, reason="soundfile not installed")
    @patch("soundfile.write")
    @patch("giva.audio.stt.STTEngine._ensure_loaded")
    def test_transcribe_audio(self, mock_ensure, mock_sf_write):
        """transcribe_audio() should save to temp file and transcribe."""
        config = VoiceConfig()
        engine = STTEngine(config)
        engine._whisper = MagicMock()
        engine._whisper.transcribe.return_value = {"text": "test transcription"}

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe_audio(audio, sample_rate=16000)

        assert result == "test transcription"
        mock_sf_write.assert_called_once()
        engine._whisper.transcribe.assert_called_once()

    @pytest.mark.skipif(
        not (_has_sounddevice and _has_soundfile),
        reason="sounddevice/soundfile not installed",
    )
    @patch("sounddevice.rec")
    @patch("sounddevice.wait")
    @patch("soundfile.write")
    @patch("giva.audio.stt.STTEngine._ensure_loaded")
    def test_record_and_transcribe(self, mock_ensure, mock_sf_write, mock_sd_wait, mock_sd_rec):
        """record_and_transcribe() should record, save, and transcribe."""
        config = VoiceConfig()
        engine = STTEngine(config)
        engine._whisper = MagicMock()
        engine._whisper.transcribe.return_value = {"text": "recorded text"}

        mock_sd_rec.return_value = np.zeros((16000 * 5, 1), dtype=np.float32)

        result = engine.record_and_transcribe(duration=5.0)

        assert result == "recorded text"
        mock_sd_rec.assert_called_once()
        mock_sd_wait.assert_called_once()
