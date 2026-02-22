"""Audio playback manager with a background thread queue.

Enqueue audio chunks and they play sequentially without blocking the caller.
Used for streaming TTS output alongside text rendering.
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np

log = logging.getLogger(__name__)

# Sentinel value to signal the player thread to stop
_STOP = None


class AudioPlayer:
    """Threaded audio playback queue for streaming TTS output."""

    def __init__(self):
        self._queue: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._playing = threading.Event()
        self._stop_requested = threading.Event()

    def _ensure_thread(self):
        """Start the playback thread if not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()

    def _playback_loop(self):
        """Background thread: dequeue and play audio chunks sequentially."""
        import sounddevice as sd

        while not self._stop_requested.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is _STOP:
                break

            audio, sample_rate = item
            self._playing.set()
            try:
                sd.play(audio, samplerate=sample_rate)
                sd.wait()
            except Exception as e:
                log.warning("Audio playback error: %s", e)
            finally:
                self._playing.clear()
                self._queue.task_done()

    def enqueue(self, audio: np.ndarray, sample_rate: int):
        """Add an audio chunk to the playback queue (non-blocking)."""
        self._ensure_thread()
        self._queue.put((audio, sample_rate))

    def play_sync(self, audio: np.ndarray, sample_rate: int):
        """Play audio synchronously (blocking)."""
        import sounddevice as sd

        sd.play(audio, samplerate=sample_rate)
        sd.wait()

    def stop(self):
        """Stop current playback and clear the queue."""
        self._stop_requested.set()

        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

        # Signal thread to exit
        self._queue.put(_STOP)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._playing.clear()

    def wait(self):
        """Wait for all queued audio to finish playing."""
        self._queue.join()

    def is_playing(self) -> bool:
        """Check if audio is currently playing."""
        return self._playing.is_set() or not self._queue.empty()
