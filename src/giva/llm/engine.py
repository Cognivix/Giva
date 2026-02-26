"""LLM engine: MLX model management with support for multiple models.

Supports a dual-model architecture:
- Filter model (small/fast, e.g. Qwen3-8B): used during sync to classify emails
- Assistant model (large/smart, e.g. Qwen3-30B): used for user queries
Both can be loaded simultaneously on M4 Max.

When ``filter_model = "apple"`` is configured, filter model calls are routed
to Apple's on-device Foundation Model (~3B) via :mod:`giva.llm.apple_adapter`
instead of loading a separate MLX model.
"""

from __future__ import annotations

import logging
import time
from typing import Generator, Optional

from giva.config import LLMConfig

log = logging.getLogger(__name__)


def _make_sampler(temp: float = 0.7, top_p: float = 0.9):
    """Create a sampler for mlx-lm generate/stream_generate."""
    from mlx_lm.sample_utils import make_sampler

    return make_sampler(temp=temp, top_p=top_p)


class ModelManager:
    """Manages multiple MLX models with lazy loading."""

    def __init__(self):
        self._models: dict[str, tuple] = {}  # model_id -> (model, tokenizer)
        self._last_use: dict[str, float] = {}  # model_id -> monotonic timestamp

    def ensure_loaded(self, model_id: str):
        """Load a model if not already loaded.

        Checks the HuggingFace cache first — if the model hasn't been
        downloaded yet, raises ``RuntimeError`` immediately instead of
        silently downloading a multi-GB model during inference.

        Apple model (``model_id="apple"``) is always "loaded" — no MLX
        weights to manage.
        """
        from giva.llm.apple_adapter import is_apple_model

        if is_apple_model(model_id) or model_id in self._models:
            return

        # Guard: refuse to auto-download large models during inference.
        # The /api/models/download endpoint handles explicit downloads.
        if not self._is_in_cache(model_id):
            raise RuntimeError(
                f"Model {model_id} is not downloaded. "
                "Use the model setup UI or /api/models/download to download it first."
            )

        log.info("Loading model %s ...", model_id)
        from mlx_lm import load

        model, tokenizer = load(model_id)
        self._models[model_id] = (model, tokenizer)
        log.info("Model %s loaded.", model_id)

    @staticmethod
    def _is_in_cache(model_id: str) -> bool:
        """Check if a model's weight files are present in the HuggingFace cache."""
        try:
            from giva.models import is_model_downloaded

            return is_model_downloaded(model_id)
        except Exception:
            return True  # Fail open — let mlx_lm.load decide

    def _get(self, model_id: str) -> tuple:
        """Get a loaded model and tokenizer."""
        self.ensure_loaded(model_id)
        self._last_use[model_id] = time.monotonic()
        return self._models[model_id]

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temp: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        """Generate a complete response.

        Routes to the Apple adapter when ``model_id`` is ``"apple"``.
        """
        from giva.llm.apple_adapter import is_apple_model

        if is_apple_model(model_id):
            from giva.llm.apple_adapter import adapter
            return adapter.generate(messages, max_tokens=max_tokens, temp=temp, top_p=top_p)

        model, tokenizer = self._get(model_id)
        from mlx_lm import generate as mlx_generate

        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        sampler = _make_sampler(temp=temp, top_p=top_p)
        return mlx_generate(
            model, tokenizer, prompt=prompt,
            max_tokens=max_tokens, sampler=sampler,
        )

    def stream_generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temp: float = 0.7,
        top_p: float = 0.9,
    ) -> Generator[str, None, None]:
        """Stream tokens from the model.

        For the Apple model, falls back to a single-shot generate since
        streaming yields snapshots (not deltas) and filter model callers
        don't typically use streaming.
        """
        from giva.llm.apple_adapter import is_apple_model

        if is_apple_model(model_id):
            # Apple's stream_response yields snapshots, not deltas.
            # Simpler and safer to return the full response as one chunk.
            from giva.llm.apple_adapter import adapter
            yield adapter.generate(messages, max_tokens=max_tokens, temp=temp, top_p=top_p)
            return

        model, tokenizer = self._get(model_id)
        from mlx_lm import stream_generate as mlx_stream

        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        sampler = _make_sampler(temp=temp, top_p=top_p)
        for response in mlx_stream(
            model, tokenizer, prompt=prompt,
            max_tokens=max_tokens, sampler=sampler,
        ):
            yield response.text

    def is_loaded(self, model_id: str) -> bool:
        from giva.llm.apple_adapter import is_apple_model

        # Apple model is always "loaded" — it's part of the OS.
        return is_apple_model(model_id) or model_id in self._models

    def unload(self, model_id: str):
        """Unload a model to free memory."""
        from giva.llm.apple_adapter import is_apple_model

        if is_apple_model(model_id):
            return  # Nothing to unload — managed by the OS

        if model_id in self._models:
            del self._models[model_id]
            self._last_use.pop(model_id, None)
            log.info("Model %s unloaded.", model_id)

    def unload_idle(self, timeout_seconds: int) -> list[str]:
        """Unload models that haven't been used for ``timeout_seconds``.

        Returns the list of model IDs that were unloaded.
        Caller must hold ``_llm_lock`` to prevent races with active inference.
        """
        now = time.monotonic()
        to_unload = [
            mid for mid, ts in self._last_use.items()
            if mid in self._models and (now - ts) > timeout_seconds
        ]
        for mid in to_unload:
            self.unload(mid)
        return to_unload

    def loaded_models(self) -> list[str]:
        return list(self._models.keys())


# Module-level singleton
manager = ModelManager()


# --- Backward-compatible module-level functions ---
# These use config.model (the assistant model) by default.


def generate(
    messages: list[dict[str, str]],
    config: LLMConfig,
    max_tokens: Optional[int] = None,
) -> str:
    """Generate a complete response using the assistant model."""
    return manager.generate(
        config.model, messages,
        max_tokens=max_tokens or config.max_tokens,
        temp=config.temperature,
        top_p=config.top_p,
    )


def stream_generate(
    messages: list[dict[str, str]],
    config: LLMConfig,
    max_tokens: Optional[int] = None,
) -> Generator[str, None, None]:
    """Stream tokens from the assistant model."""
    yield from manager.stream_generate(
        config.model, messages,
        max_tokens=max_tokens or config.max_tokens,
        temp=config.temperature,
        top_p=config.top_p,
    )


def is_loaded() -> bool:
    """Check if any model is loaded."""
    return len(manager.loaded_models()) > 0


def unload():
    """Unload all models."""
    for model_id in list(manager.loaded_models()):
        manager.unload(model_id)
