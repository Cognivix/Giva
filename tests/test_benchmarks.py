"""Tests for benchmark data fetching and processing."""

import json
from unittest.mock import patch

from giva.benchmarks import (
    _format_benchmark_summary,
    fetch_benchmark_data,
)


# --- Formatting ---


def test_format_benchmark_summary_empty():
    result = _format_benchmark_summary([], [])
    assert result == "No benchmark data available."


def test_format_benchmark_summary_with_data():
    models = [
        {"name": "Qwen3-30B", "source": "Open LLM Leaderboard", "score": 85.2, "rank": 1},
        {"name": "DeepSeek-R1", "source": "Open LLM Leaderboard", "score": 82.1, "rank": 2},
    ]
    result = _format_benchmark_summary(models, ["Open LLM Leaderboard"])
    assert "Qwen3-30B" in result
    assert "DeepSeek-R1" in result
    assert "85.2" in result
    assert "Open LLM Leaderboard" in result


def test_format_benchmark_summary_multiple_sources():
    models = [
        {"name": "ModelA", "source": "Source1", "score": 90.0, "rank": 1},
        {"name": "ModelB", "source": "Source2", "score": 1200.0, "rank": 1},
    ]
    result = _format_benchmark_summary(models, ["Source1", "Source2"])
    assert "Source1" in result
    assert "Source2" in result
    assert "ModelA" in result
    assert "ModelB" in result


# --- Cache ---


def test_fetch_benchmark_data_uses_cache(tmp_path):
    """Should return cached data when fresh."""
    import time

    cache_data = {
        "timestamp": time.time(),
        "data": {
            "top_models": [{"name": "CachedModel", "source": "Test", "score": 99.0, "rank": 1}],
            "sources_used": ["Test"],
            "raw_text": "cached text",
        },
    }
    cache_file = tmp_path / "benchmark_cache.json"
    cache_file.write_text(json.dumps(cache_data))

    result = fetch_benchmark_data(cache_dir=tmp_path)
    assert result["sources_used"] == ["Test"]
    assert result["top_models"][0]["name"] == "CachedModel"


def test_fetch_benchmark_data_stale_cache(tmp_path):
    """Should ignore stale cache and fetch fresh data."""
    cache_data = {
        "timestamp": 0,  # Very old
        "data": {
            "top_models": [],
            "sources_used": [],
            "raw_text": "stale",
        },
    }
    cache_file = tmp_path / "benchmark_cache.json"
    cache_file.write_text(json.dumps(cache_data))

    # Mock the fetchers to avoid real network calls
    with patch("giva.benchmarks._fetch_open_llm_leaderboard") as mock_ollm, \
         patch("giva.benchmarks._fetch_lmarena_elo") as mock_arena:
        mock_ollm.return_value = [("FreshModel", 88.0)]
        mock_arena.return_value = []

        result = fetch_benchmark_data(cache_dir=tmp_path)
        assert any(m["name"] == "FreshModel" for m in result["top_models"])


# --- Fetcher error handling ---


def test_fetch_benchmark_data_all_fetchers_fail(tmp_path):
    """Should return empty results gracefully when all fetchers fail."""
    with patch("giva.benchmarks._fetch_open_llm_leaderboard") as mock_ollm, \
         patch("giva.benchmarks._fetch_lmarena_elo") as mock_arena:
        mock_ollm.side_effect = Exception("network error")
        mock_arena.side_effect = Exception("network error")

        result = fetch_benchmark_data(cache_dir=tmp_path)
        assert result["sources_used"] == []
        assert result["raw_text"] == "No benchmark data available."


def test_fetch_benchmark_data_partial_success(tmp_path):
    """Should succeed with data from whichever fetchers work."""
    with patch("giva.benchmarks._fetch_open_llm_leaderboard") as mock_ollm, \
         patch("giva.benchmarks._fetch_lmarena_elo") as mock_arena:
        mock_ollm.return_value = [("TopModel", 90.0), ("SecondModel", 85.0)]
        mock_arena.side_effect = Exception("network error")

        result = fetch_benchmark_data(cache_dir=tmp_path)
        assert "Open LLM Leaderboard" in result["sources_used"]
        assert "LMArena Chatbot Arena" not in result["sources_used"]
        assert len(result["top_models"]) == 2
