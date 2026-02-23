"""Speech-to-text engine using Lightning Whisper MLX.

Provides mic recording via sounddevice and transcription via Whisper on Apple Silicon.
Lazy-loads the model on first use.

NOTE: LightningWhisperMLX hardcodes relative paths (``./mlx_models/``) for model
downloads and loading.  When running under launchd the cwd is ``/`` (read-only),
so we ``os.chdir`` to the Giva data directory before any Whisper call.  All Whisper
calls are serialised by ``_voice_lock`` in ``server.py``, so this is safe.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np

from giva.config import VoiceConfig

log = logging.getLogger(__name__)

# Directory where LightningWhisperMLX will store ``./mlx_models/``.
_WHISPER_HOME = Path("~/.local/share/giva").expanduser()


class STTEngine:
    """Lightning Whisper MLX for speech-to-text."""

    def __init__(self, config: VoiceConfig):
        self._config = config
        self._whisper = None

    def _ensure_loaded(self):
        """Load the Whisper model if not already loaded."""
        if self._whisper is not None:
            return
        log.info("Loading STT model %s ...", self._config.stt_model)
        from lightning_whisper_mlx import LightningWhisperMLX

        # LightningWhisperMLX downloads to ./mlx_models/ relative to cwd.
        # Under launchd cwd is "/" (read-only), so we chdir first.
        _WHISPER_HOME.mkdir(parents=True, exist_ok=True)
        os.chdir(_WHISPER_HOME)

        self._whisper = LightningWhisperMLX(
            model=self._config.stt_model,
            batch_size=12,
        )
        log.info("STT model loaded.")

    def transcribe_file(self, path: str | Path) -> str:
        """Transcribe an audio file (WAV, MP3, etc.) to text."""
        self._ensure_loaded()
        # Library resolves model weights via ./mlx_models/ relative to cwd.
        os.chdir(_WHISPER_HOME)
        result = self._whisper.transcribe(str(path))
        return result.get("text", "").strip()

    def transcribe_audio(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a numpy audio array to text.

        Saves to a temp WAV file then transcribes (Lightning Whisper MLX
        expects a file path).
        """
        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
            sf.write(tmp_path, audio, sample_rate, subtype="PCM_16")

        try:
            return self.transcribe_file(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def record_and_transcribe(
        self,
        duration: float = 5.0,
        sample_rate: int = 16000,
    ) -> str:
        """Record from microphone for `duration` seconds and transcribe.

        Args:
            duration: Recording length in seconds.
            sample_rate: Mic sample rate (16kHz is standard for speech recognition).

        Returns:
            Transcribed text.
        """
        import sounddevice as sd

        log.info("Recording %.1fs from microphone at %d Hz ...", duration, sample_rate)
        audio = sd.rec(
            frames=int(sample_rate * duration),
            samplerate=sample_rate,
            channels=1,
            dtype=np.float32,
        )
        sd.wait()  # Block until recording completes
        log.info("Recording complete, transcribing...")

        # Squeeze to 1-D
        audio = audio.squeeze()
        return self.transcribe_audio(audio, sample_rate)

    def record_until_silence(
        self,
        sample_rate: int = 16000,
        silence_threshold: float = 0.01,
        silence_duration: float = 1.5,
        max_duration: float = 30.0,
    ) -> str:
        """Record from microphone until silence is detected, then transcribe.

        Args:
            sample_rate: Mic sample rate.
            silence_threshold: RMS amplitude below which audio is considered silence.
            silence_duration: Seconds of continuous silence to trigger stop.
            max_duration: Maximum recording duration as a safety limit.

        Returns:
            Transcribed text.
        """
        import sounddevice as sd

        log.info("Listening... (speak now, silence will end recording)")
        block_duration = 0.1  # 100ms blocks
        block_size = int(sample_rate * block_duration)
        max_blocks = int(max_duration / block_duration)
        silence_blocks_needed = int(silence_duration / block_duration)

        audio_blocks: list[np.ndarray] = []
        silence_count = 0
        has_speech = False

        with sd.InputStream(samplerate=sample_rate, channels=1, blocksize=block_size) as stream:
            for _ in range(max_blocks):
                data, _overflowed = stream.read(block_size)
                audio_blocks.append(data.copy())

                rms = float(np.sqrt(np.mean(data**2)))
                if rms > silence_threshold:
                    has_speech = True
                    silence_count = 0
                else:
                    if has_speech:
                        silence_count += 1

                if has_speech and silence_count >= silence_blocks_needed:
                    break

        if not audio_blocks or not has_speech:
            log.info("No speech detected.")
            return ""

        audio = np.concatenate(audio_blocks).squeeze()
        log.info("Recording complete (%.1fs), transcribing...", len(audio) / sample_rate)
        return self.transcribe_audio(audio, sample_rate)

    def is_loaded(self) -> bool:
        return self._whisper is not None

    def unload(self):
        """Free model memory."""
        if self._whisper is not None:
            self._whisper = None
            log.info("STT model unloaded.")
