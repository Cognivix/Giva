"""VLM inference placeholder for mlx-vlm integration.

This module will be replaced with actual mlx-vlm model loading and inference
once the VLM model is selected and downloaded. The interface is designed to
match how mlx-vlm's generate() works with image+text prompts.
"""

from __future__ import annotations

import logging
from typing import Optional

from giva.llm.structured import VlmAction

log = logging.getLogger(__name__)


def run_vlm_inference(
    image_b64: str,
    objective: str,
    context: Optional[str] = None,
) -> VlmAction:
    """Analyze a screenshot and decide the next browser action.

    Args:
        image_b64: Base64-encoded PNG screenshot from Chrome extension.
        objective: The current subtask objective (what the VLM should accomplish).
        context: Optional additional context (previous actions, page state).

    Returns:
        VlmAction with the next action to perform.
    """
    # TODO: Replace with actual mlx-vlm inference:
    #   from mlx_vlm import load, generate
    #   model, processor = load("mlx-community/Qwen2.5-VL-7B-4bit")
    #   response = generate(model, processor, image, prompt, max_tokens=256)
    #   return _parse_vlm_response(response)
    log.warning("VLM inference called but not yet implemented — returning no-op")
    return VlmAction(
        action_type="done",
        reasoning="VLM inference not yet implemented",
        summary="VLM placeholder — no action taken.",
    )
