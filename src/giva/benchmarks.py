"""Fetch real-time LLM benchmark data from public leaderboards.

Provides fresh benchmark rankings to guide model recommendations,
avoiding reliance on potentially stale LLM training data.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Cache benchmark data for 24h to avoid repeated fetches
_BENCHMARK_CACHE_TTL = 24 * 3600


def fetch_benchmark_data(cache_dir: Optional[Path] = None) -> dict:
    """Fetch and aggregate benchmark rankings from multiple public sources.

    Returns a dict with:
        top_models: list of {"name": str, "source": str, "score": float, "rank": int}
        sources_used: list of source names that succeeded
        raw_text: formatted text summary suitable for feeding to an LLM
    """
    cache_path = _benchmark_cache_path(cache_dir)
    cached = _load_benchmark_cache(cache_path)
    if cached is not None:
        return cached

    all_models: list[dict] = []
    sources_used: list[str] = []

    # Try multiple sources — each returns a list of (name, score) tuples
    fetchers = [
        ("Open LLM Leaderboard", _fetch_open_llm_leaderboard),
        ("LMArena Chatbot Arena", _fetch_lmarena_elo),
    ]

    for source_name, fetcher in fetchers:
        try:
            models = fetcher()
            if models:
                for rank, (name, score) in enumerate(models[:30], 1):
                    all_models.append({
                        "name": name,
                        "source": source_name,
                        "score": score,
                        "rank": rank,
                    })
                sources_used.append(source_name)
                log.info("Fetched %d models from %s", len(models), source_name)
        except Exception as e:
            log.warning("Failed to fetch %s: %s", source_name, e)

    # Build LLM-readable text summary
    raw_text = _format_benchmark_summary(all_models, sources_used)

    result = {
        "top_models": all_models,
        "sources_used": sources_used,
        "raw_text": raw_text,
    }

    _save_benchmark_cache(cache_path, result)
    return result


# Known official model orgs on HuggingFace — filters out community merges/abliterations
_OFFICIAL_ORGS = {
    "qwen", "meta-llama", "google", "deepseek-ai", "mistralai", "microsoft",
    "nvidia", "01-ai", "alibaba-nlp", "tiiuae", "bigscience", "cohereforai",
    "stabilityai", "databricks", "ibm-granite", "mosaicml", "upstage",
    "internlm", "baichuan-inc", "thudm", "openbmb", "apple",
}

# Proprietary/API-only model name patterns in LMArena (no open weights)
_PROPRIETARY_PATTERNS = {
    "gemini", "gpt", "o1-", "o3-", "o4-", "claude", "grok", "chatgpt",
    "bard", "palm", "command-r", "command-a", "reka", "copilot",
    "aya-vision", "early-grok", "im-also-a-good-gpt",
    # Chinese proprietary API models
    "hunyuan", "glm-4-plus", "step-2", "doubao", "yi-lightning",
    "ernie", "spark", "minimax", "moonshot", "baichuan-",
    # Qwen API-only (not open-weight Qwen3/Qwen2.5 repo models)
    "qwen2.5-max", "qwen-plus", "qwen-max", "qwen-turbo",
    "qwen2.5-plus",
    # Other proprietary
    "amazon-nova", "phi-4-reasoning-plus",
}


def _fetch_open_llm_leaderboard() -> list[tuple[str, float]]:
    """Fetch top models from HuggingFace Open LLM Leaderboard via datasets API.

    Filters out community merges and fine-tunes to only include models from
    known official orgs (Qwen, Meta, Google, DeepSeek, etc.).

    Returns list of (model_name, average_score) sorted by score descending.
    """
    import requests

    url = "https://datasets-server.huggingface.co/rows"
    all_rows = []

    # Fetch in pages of 100 (the API max)
    for offset in range(0, 500, 100):
        resp = requests.get(url, params={
            "dataset": "open-llm-leaderboard/contents",
            "config": "default",
            "split": "train",
            "offset": offset,
            "length": 100,
        }, timeout=30)

        if resp.status_code != 200:
            if offset == 0:
                log.warning("Open LLM Leaderboard API returned %d", resp.status_code)
                return []
            break

        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)

    # Extract and filter models
    models = []
    for r in all_rows:
        row = r.get("row", {})
        name = row.get("fullname") or row.get("Model") or ""
        score = (
            row.get("Average ⬆️")
            or row.get("Average")
            or row.get("average")
            or 0
        )
        merged = row.get("Merged", False)

        if not name or not isinstance(score, (int, float)) or score <= 0:
            continue

        # Skip merged/community models — they game benchmarks
        if merged:
            continue

        # Only include models from known official orgs
        org = name.split("/")[0].lower() if "/" in name else ""
        if org not in _OFFICIAL_ORGS:
            continue

        models.append((name, float(score)))

    models.sort(key=lambda x: x[1], reverse=True)
    return models


def _fetch_lmarena_elo() -> list[tuple[str, float]]:
    """Fetch Elo rankings from LMArena community-maintained history.

    Filters out proprietary/API-only models (GPT, Gemini, Claude, etc.)
    to only include open-source/open-weight models.

    Returns list of (model_name, elo_score) sorted by Elo descending.
    """
    import requests

    url = "https://raw.githubusercontent.com/nakasyou/lmarena-history/main/output/scores.json"
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        log.warning("LMArena history returned %d", resp.status_code)
        return []

    data = resp.json()

    # Get the latest date's overall text rankings
    if not data:
        return []

    latest_date = max(data.keys())
    text_data = data.get(latest_date, {}).get("text", {})
    overall = text_data.get("overall", {})

    if not overall:
        return []

    ranked = sorted(overall.items(), key=lambda x: x[1], reverse=True)

    # Filter out proprietary models
    open_models = []
    for name, elo in ranked:
        name_lower = name.lower()
        if any(p in name_lower for p in _PROPRIETARY_PATTERNS):
            continue
        open_models.append((name, float(elo)))

    return open_models


def _format_benchmark_summary(models: list[dict], sources: list[str]) -> str:
    """Format benchmark data into a text summary for LLM consumption."""
    if not models:
        return "No benchmark data available."

    lines = ["# Current LLM Benchmark Rankings (fetched live)\n"]
    lines.append(f"Sources: {', '.join(sources)}\n")

    for source in sources:
        source_models = [m for m in models if m["source"] == source]
        if not source_models:
            continue

        lines.append(f"\n## {source} — Top 20:")
        for m in source_models[:20]:
            lines.append(f"  {m['rank']:2d}. {m['name']}  (score: {m['score']:.1f})")

    return "\n".join(lines)


def _benchmark_cache_path(cache_dir: Optional[Path] = None) -> Path:
    base = cache_dir or Path("~/.local/share/giva").expanduser()
    return base / "benchmark_cache.json"


def _load_benchmark_cache(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("timestamp", 0) < _BENCHMARK_CACHE_TTL:
            return data.get("data", {})
    except Exception:
        pass
    return None


def _save_benchmark_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"timestamp": time.time(), "data": data}))
    except Exception as e:
        log.warning("Could not save benchmark cache: %s", e)
