"""Apple Foundation Models adapter for the filter model.

Wraps Apple's on-device ~3B foundation model (via ``apple_fm_sdk``) behind
the same ``generate()`` interface that :class:`ModelManager` uses for MLX
models.  This lets users select ``filter_model = "apple"`` in their config
to use the built-in Apple Intelligence model instead of downloading a
separate HuggingFace filter model.

Requirements:
- macOS 26+ with Apple Intelligence enabled
- ``apple-fm-sdk`` installed (``pip install apple-fm-sdk`` or from source)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Sentinel value used in config to select the Apple model.
APPLE_MODEL_ID = "apple"


def is_apple_model(model_id: str) -> bool:
    """Check if a model_id refers to the Apple on-device model."""
    return model_id.lower() == APPLE_MODEL_ID


def check_apple_model_availability() -> tuple[bool, Optional[str]]:
    """Check if the Apple Foundation Model is available on this system.

    Returns (available, reason_string).  ``reason_string`` is None when
    available, otherwise a human-readable explanation.
    """
    try:
        import apple_fm_sdk as fm

        model = fm.SystemLanguageModel()
        available, reason = model.is_available()
        if available:
            return True, None

        reason_messages = {
            "APPLE_INTELLIGENCE_NOT_ENABLED": (
                "Apple Intelligence is not enabled. "
                "Enable it in System Settings > Apple Intelligence & Siri."
            ),
            "DEVICE_NOT_ELIGIBLE": (
                "This device does not support Apple Intelligence. "
                "Requires Apple Silicon (M1+)."
            ),
            "MODEL_NOT_READY": (
                "The Apple Foundation Model is still downloading or initializing. "
                "Please wait and try again."
            ),
        }
        reason_name = reason.name if reason else "UNKNOWN"
        msg = reason_messages.get(reason_name, f"Apple model unavailable: {reason_name}")
        return False, msg

    except ImportError:
        return False, (
            "apple-fm-sdk is not installed. "
            "Install it from https://github.com/apple/python-apple-fm-sdk"
        )
    except Exception as e:
        return False, f"Failed to check Apple model availability: {e}"


class AppleModelAdapter:
    """Adapter that presents Apple's Foundation Model with the same interface
    as an MLX model in :class:`ModelManager`.

    The adapter translates chat-format messages (``[{role, content}]``) into
    a single prompt string, sends it to the on-device model, and returns the
    response text.

    The Apple model is stateless between calls — each ``generate()`` creates
    a fresh :class:`LanguageModelSession` with the conversation as
    instructions + prompt.  This matches how the filter model is used: short,
    independent classification calls, not multi-turn conversations.
    """

    def __init__(self):
        self._sdk_available: Optional[bool] = None

    def _ensure_sdk(self):
        """Lazily verify that the SDK is importable."""
        if self._sdk_available is None:
            try:
                import apple_fm_sdk  # noqa: F401
                self._sdk_available = True
            except ImportError:
                self._sdk_available = False

        if not self._sdk_available:
            raise RuntimeError(
                "apple-fm-sdk is not installed. "
                "Install it from https://github.com/apple/python-apple-fm-sdk"
            )

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temp: float = 0.1,
        top_p: float = 0.9,
    ) -> str:
        """Generate a complete response from the Apple Foundation Model.

        Translates the chat messages into instructions + prompt, calls the
        on-device model, and returns the response text.

        Parameters match :meth:`ModelManager.generate` for drop-in compatibility.
        ``temp`` and ``top_p`` are accepted but ignored — the Apple model does
        not expose sampling parameters.
        """
        self._ensure_sdk()
        import apple_fm_sdk as fm

        instructions, prompt = self._messages_to_prompt(messages)

        # Run the async respond() call synchronously.
        # The filter model is always called under _llm_lock, so we're in a
        # thread-pool thread — safe to create a new event loop.
        async def _call():
            session = fm.LanguageModelSession(instructions=instructions)
            return await session.respond(prompt)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context (e.g. FastAPI thread pool).
            # Create a new loop in this thread.
            response = asyncio.run(_call())
        else:
            response = asyncio.run(_call())

        return str(response)

    @staticmethod
    def _messages_to_prompt(
        messages: list[dict[str, str]],
    ) -> tuple[str, str]:
        """Convert chat-format messages to (instructions, prompt).

        The Apple model uses ``instructions`` (system prompt) and a single
        ``prompt`` string, not a multi-turn message list.

        Strategy:
        - ``system`` messages → concatenated as instructions
        - All ``user``/``assistant`` messages → formatted as the prompt
        - The last ``user`` message is the primary prompt
        """
        system_parts: list[str] = []
        conversation_parts: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                conversation_parts.append(content)
            elif role == "assistant":
                conversation_parts.append(f"[Previous response]: {content}")

        instructions = "\n\n".join(system_parts) if system_parts else ""

        # If there's only one user message, use it directly as the prompt.
        # Otherwise, combine all conversation parts.
        if len(conversation_parts) == 1:
            prompt = conversation_parts[0]
        elif conversation_parts:
            prompt = "\n\n".join(conversation_parts)
        else:
            prompt = ""

        return instructions, prompt


# Module-level singleton
adapter = AppleModelAdapter()
