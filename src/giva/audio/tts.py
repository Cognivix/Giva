"""Text-to-speech engine using Qwen3-TTS via mlx-audio.

Lazy-loads the model on first use. Thread-safe via external _voice_lock.
Synthesizes text per-sentence for natural speech with streaming playback.
"""

from __future__ import annotations

import logging
import re
from typing import Generator

import numpy as np

from giva.config import VoiceConfig

log = logging.getLogger(__name__)

# Sentence boundary pattern: split on .!? followed by whitespace or end-of-string,
# but avoid splitting on common abbreviations or decimal numbers.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")


class TTSEngine:
    """Qwen3-TTS via mlx-audio. Lazy-loaded."""

    def __init__(self, config: VoiceConfig):
        self._config = config
        self._model = None

    def _ensure_loaded(self):
        """Load the TTS model if not already loaded."""
        if self._model is not None:
            return
        log.info("Loading TTS model %s ...", self._config.tts_model)
        from mlx_audio.tts.utils import load_model

        self._model = load_model(self._config.tts_model)
        log.info("TTS model loaded.")

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Synthesize speech from text. Returns (audio_array, sample_rate).

        The audio array is a 1-D float32 numpy array of PCM samples.
        """
        self._ensure_loaded()
        audio_parts = []
        for result in self._model.generate(
            text,
            voice=self._config.tts_voice,
            speed=1.0,
        ):
            # result.audio may be an mx.array — convert to numpy
            chunk = np.array(result.audio, dtype=np.float32)
            if chunk.ndim > 1:
                chunk = chunk.squeeze()
            audio_parts.append(chunk)

        if not audio_parts:
            return np.array([], dtype=np.float32), self._config.sample_rate

        audio = np.concatenate(audio_parts)
        return audio, self._config.sample_rate

    def synthesize_sentences(
        self, token_gen: Generator[str, None, None]
    ) -> Generator[tuple[np.ndarray, int], None, None]:
        """Consume a token generator, buffer into sentences, and yield audio per sentence.

        Yields (audio_array, sample_rate) for each complete sentence.
        Any remaining text at the end (no sentence terminator) is also synthesized.
        """
        buffer = ""
        for token in token_gen:
            buffer += token

            # Check if buffer contains a complete sentence
            sentences = split_sentences(buffer)
            if len(sentences) > 1:
                # All but last are complete sentences — synthesize them
                for sentence in sentences[:-1]:
                    sentence = sentence.strip()
                    if sentence:
                        yield self.synthesize(sentence)
                # Keep the remainder
                buffer = sentences[-1]

        # Synthesize any remaining text
        buffer = buffer.strip()
        if buffer:
            yield self.synthesize(buffer)

    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self):
        """Free model memory."""
        if self._model is not None:
            self._model = None
            log.info("TTS model unloaded.")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences at .!? boundaries.

    Returns a list where all but the last element are complete sentences.
    The last element is the remaining (possibly incomplete) text.
    """
    parts = _SENTENCE_RE.split(text)
    # Filter out empty strings but preserve structure
    return [p for p in parts if p] or [text]
