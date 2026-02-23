"""Pluggable agent system for Giva.

Agents are specialized modules that the main LLM can delegate to.
Each agent self-describes via an AgentManifest and is discovered
by the AgentRegistry at startup.
"""

from giva.agents.base import Agent, AgentManifest, AgentResult, BaseAgent
from giva.agents.registry import registry

__all__ = ["Agent", "AgentManifest", "AgentResult", "BaseAgent", "registry"]
