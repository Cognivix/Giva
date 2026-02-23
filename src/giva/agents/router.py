"""Agent router: matches user queries to specialized agents.

Two-stage routing:
1. Keyword pre-filter (zero cost) — checks if query tokens overlap with
   agent manifest keywords. If no overlap, skip LLM routing entirely.
2. LLM classification (filter model, ~0.3s) — only runs when pre-filter
   matches. Returns the agent_id or "none".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from giva.agents.base import AgentManifest, AgentResult
from giva.agents.registry import registry
from giva.config import GivaConfig
from giva.db.store import Store

log = logging.getLogger(__name__)

ROUTE_PROMPT = """Given this user query and the available specialized agents below, \
decide if any agent should handle the request.

User query: {query}

Available agents:
{catalog}

If an agent should handle this, respond with ONLY:
{{"agent_id": "the_id", "extracted_params": {{"key": "value"}}}}

If none of the agents are relevant and this is a normal chat question, respond with:
{{"agent_id": "none"}}

/no_think"""

# Words too common to trigger agent routing
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "his", "how", "its", "may",
    "new", "now", "old", "see", "way", "who", "did", "get", "let", "say",
    "she", "too", "use", "with", "this", "that", "from", "have", "been",
    "will", "what", "when", "where", "which", "about", "could", "would",
    "should", "there", "their", "them", "then", "than", "some", "into",
    "other", "also", "just", "more", "make", "like", "time", "very",
    "your", "want", "need", "help", "please", "could", "know",
})


def keyword_prefilter(
    query: str, manifests: list[AgentManifest]
) -> list[AgentManifest]:
    """Fast keyword check: does the query share tokens with any agent?

    Returns agent manifests with at least one meaningful keyword overlap.
    Zero LLM cost — runs on every query.
    """
    query_words = {
        w.lower()
        for w in re.split(r"\W+", query)
        if len(w) > 2 and w.lower() not in _STOP_WORDS
    }
    if not query_words:
        return []

    candidates = []
    for m in manifests:
        agent_words = {
            w.lower()
            for text in [m.name, m.description] + m.examples
            for w in re.split(r"\W+", text)
            if len(w) > 2 and w.lower() not in _STOP_WORDS
        }
        overlap = query_words & agent_words
        if len(overlap) >= 1:
            candidates.append(m)
    return candidates


def route_query(
    query: str,
    config: GivaConfig,
) -> Optional[tuple[str, dict]]:
    """Route a query to a specialized agent or return None for normal chat.

    Returns (agent_id, extracted_params) or None.
    Caller is responsible for holding _llm_lock before calling this.
    """
    manifests = registry.list_manifests()
    if not manifests:
        return None

    # Stage 1: keyword pre-filter
    candidates = keyword_prefilter(query, manifests)
    if not candidates:
        return None

    # Stage 2: LLM classification (filter model)
    catalog_lines = []
    for m in candidates:
        catalog_lines.append(f"- {m.agent_id}: {m.name} — {m.description}")
        for ex in m.examples[:2]:
            catalog_lines.append(f'    example: "{ex}"')

    prompt = ROUTE_PROMPT.format(
        query=query[:500],
        catalog="\n".join(catalog_lines),
    )

    from giva.llm.engine import manager

    try:
        raw = manager.generate(
            config.llm.filter_model,
            [{"role": "user", "content": prompt}],
            max_tokens=128,
            temp=0.1,
            top_p=0.9,
        )

        # Parse with fail-safe JSON extraction
        raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            agent_id = result.get("agent_id", "none")
            if agent_id != "none" and registry.get(agent_id):
                params = result.get("extracted_params", {})
                if not isinstance(params, dict):
                    params = {}
                log.info("Router selected agent: %s (params=%s)", agent_id, params)
                return (agent_id, params)
    except Exception as e:
        log.debug("Agent routing error: %s", e)

    return None


def execute_agent(
    agent_id: str,
    query: str,
    context: dict,
    store: Store,
    config: GivaConfig,
) -> AgentResult:
    """Execute a specific agent. Caller holds _llm_lock.

    Returns AgentResult. Never raises — catches exceptions and returns
    an error result.
    """
    agent = registry.get(agent_id)
    if agent is None:
        return AgentResult(
            success=False,
            output="",
            error=f"Agent '{agent_id}' not found",
        )

    try:
        result = agent.execute(query, context, store, config)
        log.info(
            "Agent %s executed (success=%s, actions=%d)",
            agent_id,
            result.success,
            len(result.actions),
        )
        return result
    except Exception as e:
        log.error("Agent %s execution error: %s", agent_id, e)
        return AgentResult(
            success=False,
            output="",
            error=str(e),
        )
