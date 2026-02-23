"""Base agent protocol and shared infrastructure for pluggable agents.

Defines the Agent Protocol (what agents must implement), AgentManifest
(self-describing metadata), AgentResult (structured output), and BaseAgent
(convenience base class with LLM, AppleScript, and JSON parsing helpers).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional, Protocol, runtime_checkable

from giva.config import GivaConfig
from giva.db.store import Store


@dataclass(frozen=True)
class AgentManifest:
    """Self-describing metadata for agent discovery and LLM-based routing.

    Frozen dataclass following the project convention for config objects.
    The router sees name, description, and examples when deciding which
    agent to invoke.
    """

    agent_id: str           # unique slug: "email_drafter", "safari_agent"
    name: str               # human-readable: "Email Drafter"
    description: str        # 1-2 sentence capability summary
    examples: list[str] = field(default_factory=list)  # 3-5 example queries
    model_tier: str = "filter"       # "filter" | "assistant"
    supports_streaming: bool = False
    requires_confirmation: bool = False  # True for external/irreversible actions
    version: str = "0.1.0"


@dataclass(frozen=True)
class AgentResult:
    """Structured output from an agent execution.

    Frozen dataclass for immutability after creation. The `actions` list
    uses the same dict format as the post-chat agent pipeline for SSE
    broadcasting compatibility.
    """

    success: bool
    output: str                                         # Primary text for user
    actions: list[dict] = field(default_factory=list)   # SSE-compatible action dicts
    artifacts: dict = field(default_factory=dict)       # Structured data (draft, path, etc.)
    error: Optional[str] = None


@runtime_checkable
class Agent(Protocol):
    """Protocol that all pluggable agents must satisfy."""

    @property
    def manifest(self) -> AgentManifest: ...

    def execute(
        self,
        query: str,
        context: dict,
        store: Store,
        config: GivaConfig,
    ) -> AgentResult: ...


class BaseAgent:
    """Shared infrastructure for agents. Not required but recommended.

    Provides convenience methods for LLM calls, AppleScript execution,
    JSON parsing, and per-agent file storage. Agents can inherit from this
    or implement the Agent Protocol directly.

    IMPORTANT: _llm_generate and _llm_stream do NOT acquire _llm_lock.
    The caller (server/router) is responsible for lock management.
    """

    def __init__(self, manifest: AgentManifest):
        self._manifest = manifest
        self.log = logging.getLogger(f"giva.agents.{manifest.agent_id}")

    @property
    def manifest(self) -> AgentManifest:
        return self._manifest

    def _llm_generate(
        self,
        config: GivaConfig,
        messages: list[dict],
        max_tokens: int = 1024,
        temp: float = 0.3,
    ) -> str:
        """Generate using the agent's assigned model tier."""
        from giva.llm.engine import manager

        model_id = (
            config.llm.filter_model
            if self._manifest.model_tier == "filter"
            else config.llm.model
        )
        return manager.generate(model_id, messages, max_tokens=max_tokens, temp=temp)

    def _llm_stream(
        self,
        config: GivaConfig,
        messages: list[dict],
        max_tokens: int = 1024,
        temp: float = 0.7,
    ) -> Generator[str, None, None]:
        """Stream from the agent's assigned model tier."""
        from giva.llm.engine import manager

        model_id = (
            config.llm.filter_model
            if self._manifest.model_tier == "filter"
            else config.llm.model
        )
        yield from manager.stream_generate(model_id, messages, max_tokens=max_tokens, temp=temp)

    def _run_applescript(self, script: str, timeout: int = 120) -> str:
        """Run AppleScript via the existing utility."""
        from giva.utils.applescript import run_applescript

        return run_applescript(script, timeout=timeout)

    def _run_jxa(self, script: str, timeout: int = 120) -> str:
        """Run JXA via the existing utility."""
        from giva.utils.applescript import run_jxa

        return run_jxa(script, timeout=timeout)

    def _data_dir(self, config: GivaConfig) -> Path:
        """Return per-agent file storage directory, creating if needed.

        Location: ~/.local/share/giva/agents/<agent_id>/
        """
        d = config.data_dir / "agents" / self._manifest.agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _parse_json_safe(self, raw: str) -> Optional[dict]:
        """Fail-safe JSON extraction following the project pattern.

        Tries: direct parse → markdown code block → raw JSON object.
        Returns None on all failures instead of raising.
        """
        raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)

        # Try direct parse
        try:
            parsed = json.loads(raw.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Extract JSON from markdown code block
        md_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if md_match:
            try:
                parsed = json.loads(md_match.group(1).strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Extract first JSON object
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        self.log.debug("Failed to parse agent JSON: %s", raw[:200])
        return None
