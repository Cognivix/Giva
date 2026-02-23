"""Agent registry: discovers, loads, and indexes pluggable agents."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Optional

from giva.agents.base import Agent, AgentManifest

log = logging.getLogger(__name__)


class AgentRegistry:
    """Singleton registry of all available agents.

    Discovery: imports all sub-modules/packages in giva.agents.* and
    collects classes that satisfy the Agent Protocol.

    Convention: each agent module exports either AGENT_CLASS (a class
    to instantiate) or agent_factory() (a callable returning an instance).
    """

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Manually register an agent instance."""
        if not isinstance(agent, Agent):
            raise TypeError(f"{agent} does not satisfy the Agent protocol")
        agent_id = agent.manifest.agent_id
        if agent_id in self._agents:
            log.warning("Agent %s already registered, replacing", agent_id)
        self._agents[agent_id] = agent
        log.info("Registered agent: %s (%s)", agent_id, agent.manifest.name)

    def discover(self, db_path=None) -> int:
        """Auto-discover agents in giva.agents.* sub-modules/packages.

        Returns the count of newly discovered agents.
        If db_path is provided, runs any agent schema() SQL against it.
        """
        import giva.agents as agents_pkg

        skip = {"giva.agents.base", "giva.agents.registry", "giva.agents.router"}
        count = 0

        for _importer, modname, _ispkg in pkgutil.iter_modules(
            agents_pkg.__path__, prefix="giva.agents."
        ):
            if modname in skip:
                continue
            try:
                mod = importlib.import_module(modname)

                # Convention 1: module defines AGENT_CLASS constant
                cls = getattr(mod, "AGENT_CLASS", None)
                if cls is not None:
                    instance = cls()
                    self.register(instance)
                    self._run_schema(instance, db_path)
                    count += 1
                    continue

                # Convention 2: module defines agent_factory() callable
                # May return a single agent or a list (e.g. MCP creates
                # one agent per configured server).
                factory = getattr(mod, "agent_factory", None)
                if callable(factory):
                    result = factory()
                    if isinstance(result, list):
                        for inst in result:
                            self.register(inst)
                            self._run_schema(inst, db_path)
                            count += 1
                    else:
                        self.register(result)
                        self._run_schema(result, db_path)
                        count += 1
                    continue
            except Exception as e:
                log.warning("Failed to load agent module %s: %s", modname, e)

        return count

    def _run_schema(self, agent: Agent, db_path) -> None:
        """Run agent's schema() SQL if it defines one."""
        schema_fn = getattr(agent, "schema", None)
        if not callable(schema_fn):
            return
        try:
            sql = schema_fn()
            if sql and db_path:
                import sqlite3

                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA foreign_keys=ON")
                try:
                    conn.executescript(sql)
                    conn.commit()
                finally:
                    conn.close()
                log.info(
                    "Ran schema for agent %s", agent.manifest.agent_id
                )
        except Exception as e:
            log.warning(
                "Agent %s schema() failed: %s", agent.manifest.agent_id, e
            )

    def get(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by its ID."""
        return self._agents.get(agent_id)

    def list_manifests(self) -> list[AgentManifest]:
        """Return all registered agent manifests."""
        return [a.manifest for a in self._agents.values()]

    def has_agents(self) -> bool:
        """Quick check if any agents are registered."""
        return bool(self._agents)

    def catalog_text(self) -> str:
        """Format the catalog as text for the LLM routing prompt.

        NOT injected into the main system prompt — only used by the router
        when it needs to classify a query against available agents.
        """
        if not self._agents:
            return "No specialized agents available."
        lines = []
        for agent in self._agents.values():
            m = agent.manifest
            lines.append(f"- {m.agent_id}: {m.name} — {m.description}")
            for ex in m.examples[:3]:
                lines.append(f'    example: "{ex}"')
            if m.requires_confirmation:
                lines.append("    (requires user confirmation)")
        return "\n".join(lines)


# Module-level singleton (matches engine.py: `manager = ModelManager()`)
registry = AgentRegistry()
