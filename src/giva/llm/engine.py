"""LLM engine: MLX model management with support for multiple models.

Supports a dual-model architecture:
- Filter model (small/fast, e.g. Qwen3-8B): used during sync to classify emails
- Assistant model (large/smart, e.g. Qwen3-30B): used for user queries
Both can be loaded simultaneously on M4 Max.
"""

from __future__ import annotations

import logging
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

    def ensure_loaded(self, model_id: str):
        """Load a model if not already loaded.

        Checks the HuggingFace cache first — if the model hasn't been
        downloaded yet, raises ``RuntimeError`` immediately instead of
        silently downloading a multi-GB model during inference.
        """
        if model_id in self._models:
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
        return self._models[model_id]

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temp: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        """Generate a complete response."""
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
        """Stream tokens from the model."""
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
        return model_id in self._models

    def unload(self, model_id: str):
        """Unload a model to free memory."""
        if model_id in self._models:
            del self._models[model_id]
            log.info("Model %s unloaded.", model_id)

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
