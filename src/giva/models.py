"""Model discovery, recommendation, and download management.

Queries HuggingFace Hub for MLX-compatible models, recommends optimal choices
based on hardware specs, and manages model downloads with progress tracking.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from giva.hardware import max_model_size_gb

log = logging.getLogger(__name__)

# Cache file for model listings (avoid hammering HF API)
_CACHE_TTL_SECONDS = 24 * 3600  # 24 hours

# Default model that fits any M-series Mac
DEFAULT_MODEL = "mlx-community/Qwen3-8B-4bit"

# Prompt for LLM-based model recommendation
_RECOMMEND_PROMPT = """You are a system configuration assistant. Pick the best MLX models for a \
personal assistant app on Apple Silicon.

Hardware: {chip}, {ram_gb}GB unified memory, {gpu_cores} GPU cores
Budget: {max_size_gb}GB total (both models combined). Min assistant size: {min_assistant_gb:.0f}GB.

ASSISTANT MODEL CANDIDATES (pick one — these are all large enough for your hardware):
{assistant_table}

FILTER MODEL CANDIDATES (pick one — small model for fast email classification):
{filter_table}

Rules:
1. Pick the smartest assistant that fits. On {ram_gb}GB RAM, use the budget — bigger is better.
2. Prefer reasoning/thinking models (top on LiveBench, LMSYS Arena benchmarks).
3. Prefer MoE ("-A3B-", "-A22B-") — much faster on Apple Silicon.
4. Prefer Qwen3 family (well-tested), then DeepSeek, then Llama.
5. Prefer 4-bit quantization. Prefer "Instruct" over base models.
6. Avoid "Coder" models (too specialized).
7. Filter model: pick the smallest Qwen3 model.

Respond with ONLY JSON:
{{"assistant": "exact_model_id", "filter": "exact_model_id", "reasoning": "why"}} /no_think"""


def list_mlx_models(
    cache_dir: Optional[Path] = None,
    extra_keywords: Optional[list[str]] = None,
) -> list[dict]:
    """Query HuggingFace Hub for MLX text-generation models.

    Runs multiple searches to build a comprehensive list:
    1. Top 100 by downloads (catches popular small/medium models)
    2. Top 100 by recently modified (catches newer large models)
    3. Targeted keyword searches from extra_keywords (e.g. LLM-suggested families)

    Args:
        cache_dir: Override cache directory.
        extra_keywords: Additional model family names to search for.

    Returns a list of dicts with keys: model_id, size_gb, params, quant, downloads.
    Results are cached for 24h.
    """
    cache_path = _cache_path(cache_dir)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    from huggingface_hub import HfApi

    api = HfApi()
    seen_ids: set[str] = set()
    models: list[dict] = []

    def _collect(raw_models):
        for m in raw_models:
            model_id = m.id
            if model_id in seen_ids:
                continue
            name_lower = model_id.lower()
            if any(k in name_lower for k in ("embedding", "reward", "reranker", "gguf")):
                continue
            params, quant = _parse_model_name(model_id)
            if not params:
                continue
            seen_ids.add(model_id)
            models.append({
                "model_id": model_id,
                "size_gb": _estimate_size_gb(params, quant),
                "params": params,
                "quant": quant,
                "downloads": m.downloads or 0,
            })

    # Search 1: most popular (good for filter model candidates)
    _collect(api.list_models(
        author="mlx-community",
        pipeline_tag="text-generation",
        sort="downloads",
        limit=100,
    ))

    # Search 2: recently modified (catches newer large models)
    _collect(api.list_models(
        author="mlx-community",
        pipeline_tag="text-generation",
        sort="lastModified",
        limit=100,
    ))

    # Search 3: targeted keyword searches for specific model families
    keywords = list(extra_keywords or [])
    for keyword in keywords:
        try:
            _collect(api.list_models(
                author="mlx-community",
                search=keyword,
                pipeline_tag="text-generation",
                sort="downloads",
                limit=20,
            ))
        except Exception:
            pass  # Non-critical

    # Enrich top models with actual file sizes from HF
    # Prioritize large models (most likely to be chosen as assistant)
    by_size = sorted(models, key=lambda m: m["size_gb"], reverse=True)
    _enrich_sizes(api, by_size[:40])

    _save_cache(cache_path, models)
    return models


# Prompt for phase 1: analyze real benchmark data and suggest search keywords
_BENCHMARK_ANALYSIS_PROMPT = """You are given REAL benchmark data fetched live from public leaderboards.

{benchmark_data}

Based on this data, identify the top open-source model families for a personal assistant on \
Apple Silicon (MLX). I need search keywords to find their MLX-quantized versions on HuggingFace \
(under "mlx-community").

Return ONLY a JSON array of short model family search keywords.
Example: ["Qwen3", "DeepSeek-R1", "Llama-3.3", "Gemma-3"]

Rules:
- Extract 8-12 model family names from the benchmark rankings above
- Focus on reasoning, instruction-following, and general quality
- Include both large families (for high-RAM users) and small/efficient ones
- Use the short prefix that would match HuggingFace model IDs (e.g. "Qwen3" not "Qwen/Qwen3-...")
- Strip provider prefixes, version suffixes, and API-only model names
- Only include open-source/open-weight models that could have MLX versions

Return ONLY the JSON array, no other text. /no_think"""

# Prompt for iterative refinement: LLM reviews HF search results and suggests more keywords
_REFINE_SEARCH_PROMPT = """I searched HuggingFace (mlx-community) and found these MLX models matching \
your suggested keywords:

{found_models}

Missing from HuggingFace (no MLX version found):
{missing_keywords}

Based on the benchmark data, are there additional model families I should search for? \
Consider aliases, newer versions, or related models.

Return ONLY a JSON array of additional search keywords to try, or an empty array [] if \
the search is complete. /no_think"""


def filter_compatible_models(models: list[dict], max_size: float) -> list[dict]:
    """Filter models that fit within the given size limit in GB."""
    return [m for m in models if m["size_gb"] <= max_size]


def discover_benchmark_keywords(config=None) -> list[str]:
    """Fetch real benchmark data, feed it to the LLM, and extract search keywords.

    Flow:
    1. Fetch live benchmark rankings from public leaderboards (OpenLLM, LMArena)
    2. Feed the real data to the LLM for analysis
    3. LLM extracts model family keywords from actual rankings

    Returns a list of search keywords like ["Qwen3", "DeepSeek-R1", "Llama-3.3"].
    Falls back to a static list if both fetching and LLM fail.
    """
    from giva.benchmarks import fetch_benchmark_data

    cache_dir = config.data_dir if config else None

    # Step 1: Fetch real benchmark data from the internet
    benchmark = fetch_benchmark_data(cache_dir=cache_dir)
    benchmark_text = benchmark.get("raw_text", "")

    if not benchmark_text or benchmark_text == "No benchmark data available.":
        log.warning("No benchmark data fetched, using static fallback keywords")
        return _FALLBACK_KEYWORDS[:]

    log.info("Fetched benchmark data from: %s", benchmark.get("sources_used", []))

    # Step 2: Feed real benchmark data to LLM for analysis
    try:
        from giva.llm.engine import manager

        model_id = DEFAULT_MODEL
        if config:
            model_id = config.llm.filter_model or DEFAULT_MODEL

        prompt = _BENCHMARK_ANALYSIS_PROMPT.format(benchmark_data=benchmark_text)

        response = manager.generate(
            model_id,
            [
                {"role": "system", "content": "You are an AI expert analyzing benchmark data."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temp=0.1,
            top_p=0.95,
        )
        keywords = _parse_keyword_list(response)
        if keywords:
            log.info("LLM extracted benchmark keywords: %s", keywords)
            return keywords
    except Exception as e:
        log.warning("LLM benchmark analysis failed: %s", e)

    # Step 3: If LLM fails, extract keywords directly from benchmark model names
    return _extract_keywords_from_benchmarks(benchmark)


def refine_model_search(
    initial_keywords: list[str],
    found_models: list[dict],
    config=None,
) -> list[str]:
    """Let the LLM review HF search results and suggest additional keywords.

    This enables an iterative search: initial keywords → HF search → LLM reviews
    what was found/missing → suggests more keywords → search again.

    Returns additional keywords to search, or empty list if search is complete.
    """
    try:
        from giva.llm.engine import manager

        model_id = DEFAULT_MODEL
        if config:
            model_id = config.llm.filter_model or DEFAULT_MODEL

        # Summarize what we found
        found_families = set()
        for m in found_models:
            parts = m["model_id"].split("/")[-1].split("-")
            if parts:
                found_families.add(parts[0])

        found_summary = ", ".join(sorted(found_families)[:20])

        # Check which keywords didn't match anything
        found_lower = {m["model_id"].lower() for m in found_models}
        missing = [
            kw for kw in initial_keywords
            if not any(kw.lower() in mid for mid in found_lower)
        ]

        prompt = _REFINE_SEARCH_PROMPT.format(
            found_models=found_summary or "None",
            missing_keywords=", ".join(missing) if missing else "None (all found)",
        )

        response = manager.generate(
            model_id,
            [
                {"role": "system", "content": "You are an AI expert."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temp=0.1,
            top_p=0.95,
        )
        extra = _parse_keyword_list(response)
        if extra:
            log.info("LLM suggested additional keywords: %s", extra)
            return extra
    except Exception as e:
        log.warning("LLM refinement failed: %s", e)

    return []


_FALLBACK_KEYWORDS = [
    "Qwen3", "DeepSeek", "Llama-3.3", "Llama-4", "Gemma-3", "Mistral", "Phi-4",
]


def _extract_keywords_from_benchmarks(benchmark: dict) -> list[str]:
    """Extract model family keywords directly from benchmark model names.

    Fallback when LLM is unavailable — uses simple heuristics to extract
    family names from the benchmark ranking entries.
    """
    names = [m["name"] for m in benchmark.get("top_models", [])]
    families: dict[str, int] = {}  # family → best rank

    for name in names:
        # Clean up common prefixes and extract the family name
        clean = name.strip()
        # Remove org prefixes like "meta-llama/", "Qwen/", etc.
        if "/" in clean:
            clean = clean.split("/")[-1]

        # Extract family prefix (first 1-2 hyphen-separated parts)
        parts = clean.split("-")
        family = parts[0]

        # Include version number if it looks like "3.3" or "4" (not param counts like "72B")
        if len(parts) > 1 and re.match(r"^\d+(\.\d+)?$", parts[1]):
            family = f"{parts[0]}-{parts[1]}"

        if family and family not in families:
            families[family] = len(families)

    # Return top families, limited to 12
    sorted_families = sorted(families.items(), key=lambda x: x[1])
    keywords = [f for f, _ in sorted_families[:12]]

    if keywords:
        log.info("Extracted benchmark keywords from names: %s", keywords)
        return keywords

    return _FALLBACK_KEYWORDS[:]


def recommend_models(
    hardware_info: dict,
    compatible_models: list[dict],
    config=None,
) -> dict:
    """Use the default LLM to recommend optimal assistant + filter models.

    Presents size-filtered candidates split by role (large for assistant, small for filter)
    so the LLM can't pick an absurdly small assistant.

    Returns {"assistant": model_id, "filter": model_id, "reasoning": str}.
    Falls back to heuristic if LLM fails.
    """
    ram_gb = hardware_info.get("ram_gb", 8)
    max_size = max_model_size_gb(ram_gb)
    min_assistant_size = max_size * 0.2

    # Split candidates: large for assistant, small for filter
    assistant_candidates = sorted(
        [m for m in compatible_models if m["size_gb"] >= min_assistant_size],
        key=lambda m: m["size_gb"],
        reverse=True,
    )
    filter_candidates = sorted(
        [m for m in compatible_models if m["size_gb"] <= 10],
        key=lambda m: m["size_gb"],
    )

    # Build model tables for the prompt — show only relevant models per role
    assistant_lines = []
    for m in assistant_candidates[:25]:
        assistant_lines.append(
            f"- {m['model_id']}  size={m['size_gb']:.1f}GB  "
            f"params={m['params']}  quant={m['quant']}  downloads={m['downloads']}"
        )
    filter_lines = []
    for m in filter_candidates[:10]:
        filter_lines.append(
            f"- {m['model_id']}  size={m['size_gb']:.1f}GB  "
            f"params={m['params']}  quant={m['quant']}"
        )

    assistant_table = "\n".join(assistant_lines) if assistant_lines else "None found."
    filter_table = "\n".join(filter_lines) if filter_lines else "None found."

    prompt = _RECOMMEND_PROMPT.format(
        chip=hardware_info.get("chip", "Unknown"),
        ram_gb=ram_gb,
        gpu_cores=hardware_info.get("gpu_cores", 0),
        max_size_gb=max_size,
        min_assistant_gb=min_assistant_size,
        assistant_table=assistant_table,
        filter_table=filter_table,
    )

    # Try LLM recommendation
    try:
        from giva.llm.engine import manager

        model_id = DEFAULT_MODEL
        if config:
            model_id = config.llm.filter_model or DEFAULT_MODEL

        response = manager.generate(
            model_id,
            [
                {"role": "system", "content": "You are a helpful system configuration assistant."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temp=0.1,
            top_p=0.95,
        )
        result = _parse_recommendation(response)
        if result and _validate_recommendation(result, compatible_models, max_size):
            return result
        elif result:
            log.warning(
                "LLM recommendation rejected (too small): assistant=%s", result["assistant"]
            )
    except Exception as e:
        log.warning("LLM recommendation failed, using heuristic: %s", e)

    return _heuristic_recommendation(compatible_models, max_size)


def download_model(model_id: str, on_progress=None) -> None:
    """Download a model via huggingface_hub.

    Args:
        model_id: HuggingFace model ID (e.g. "mlx-community/Qwen3-8B-4bit")
        on_progress: Optional callback(percent, downloaded_mb, total_mb)
    """
    from huggingface_hub import snapshot_download

    log.info("Downloading model %s ...", model_id)

    if on_progress:
        # Get total size first
        total_bytes = _get_repo_size_bytes(model_id)
        total_mb = total_bytes / (1024 ** 2) if total_bytes else 0

        # snapshot_download doesn't support progress callbacks directly,
        # but we can monitor the cache directory
        snapshot_download(model_id)

        # Signal completion
        on_progress(100.0, total_mb, total_mb)
    else:
        snapshot_download(model_id)

    log.info("Model %s downloaded.", model_id)


_WEIGHT_EXTS = (".safetensors", ".bin", ".gguf")


def is_model_downloaded(model_id: str) -> bool:
    """Check if a model is fully downloaded in the HuggingFace cache.

    A model is considered downloaded only if:
    1. It has at least one weight file (.safetensors/.bin/.gguf) in the cache
    2. There are NO ``.incomplete`` temp files (indicating an interrupted download)
    3. All weight symlinks in the snapshot resolve to real files

    This catches partial downloads that ``scan_cache_dir()`` reports as
    present because some shards completed before the download was interrupted.
    """
    status = get_model_download_status(model_id)
    return status == "complete"


_SHARD_PATTERN = re.compile(r"-(\d+)-of-(\d+)\.")


def get_model_download_status(model_id: str) -> str:
    """Return the download status of a model in the HuggingFace cache.

    Returns one of:
    - ``"complete"``: all weight files present, no incomplete temps,
      all shards accounted for in multi-shard models
    - ``"partial"``: some weight files present but download was interrupted
      (detected via ``.incomplete`` temps or missing shards)
    - ``"not_downloaded"``: no weight files in cache
    """
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / ("models--" + model_id.replace("/", "--"))

    if not model_dir.is_dir():
        return "not_downloaded"

    blobs_dir = model_dir / "blobs"
    if not blobs_dir.is_dir():
        return "not_downloaded"

    # Check for .incomplete temp files (download was interrupted mid-shard)
    has_incomplete = any(
        f.name.endswith(".incomplete")
        for f in blobs_dir.iterdir()
        if f.is_file()
    )

    # Find the current snapshot (via refs/main)
    refs_main = model_dir / "refs" / "main"
    if not refs_main.exists():
        return "partial" if has_incomplete else "not_downloaded"

    try:
        commit_hash = refs_main.read_text().strip()
    except OSError:
        return "partial" if has_incomplete else "not_downloaded"

    snap_dir = model_dir / "snapshots" / commit_hash
    if not snap_dir.is_dir():
        return "partial" if has_incomplete else "not_downloaded"

    # Count weight files in the snapshot that resolve to real blobs
    weight_files: list[str] = []
    for f in snap_dir.iterdir():
        if any(f.name.endswith(ext) for ext in _WEIGHT_EXTS):
            # Symlink must resolve to a real file (not broken)
            target = f.resolve()
            if target.exists() and target.stat().st_size > 0:
                weight_files.append(f.name)

    if not weight_files:
        return "not_downloaded"

    if has_incomplete:
        return "partial"

    # For multi-shard models (e.g. model-00001-of-00017.safetensors),
    # verify all shards are present.
    expected_total = 0
    for name in weight_files:
        m = _SHARD_PATTERN.search(name)
        if m:
            total = int(m.group(2))
            if total > expected_total:
                expected_total = total

    if expected_total > 0 and len(weight_files) < expected_total:
        # Some shards are missing — incomplete download
        log.debug(
            "Model %s has %d/%d shards", model_id,
            len(weight_files), expected_total,
        )
        return "partial"

    return "complete"


def get_downloaded_model_ids() -> set[str]:
    """Return the set of model IDs that are fully downloaded in the cache.

    Only includes models where all weight shards are complete (no
    ``.incomplete`` temp files from interrupted downloads).
    """
    try:
        from huggingface_hub import scan_cache_dir

        cache = scan_cache_dir()
        ids: set[str] = set()
        for repo in cache.repos:
            if get_model_download_status(repo.repo_id) == "complete":
                ids.add(repo.repo_id)
        return ids
    except Exception:
        return set()


def get_all_cached_model_ids() -> dict[str, str]:
    """Return all model IDs in the cache with their download status.

    Returns a dict of ``{model_id: status}`` where status is one of
    ``"complete"``, ``"partial"``, ``"not_downloaded"``.
    """
    try:
        from huggingface_hub import scan_cache_dir

        cache = scan_cache_dir()
        result: dict[str, str] = {}
        for repo in cache.repos:
            status = get_model_download_status(repo.repo_id)
            if status != "not_downloaded":
                result[repo.repo_id] = status
        return result
    except Exception:
        return {}


def cleanup_incomplete_download(model_id: str) -> int:
    """Remove .incomplete temp files for a model.

    Returns the number of bytes freed.  Use this when a download cannot
    be resumed and the partial files are wasting disk space.
    """
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / ("models--" + model_id.replace("/", "--"))
    blobs_dir = model_dir / "blobs"

    if not blobs_dir.is_dir():
        return 0

    freed = 0
    for f in blobs_dir.iterdir():
        if f.is_file() and f.name.endswith(".incomplete"):
            try:
                freed += f.stat().st_size
                f.unlink()
                log.info("Removed incomplete blob: %s (%d MB)",
                         f.name[:16], freed // (1024 ** 2))
            except OSError as e:
                log.warning("Failed to remove %s: %s", f.name, e)
    return freed


def get_model_size_gb(model_id: str) -> float:
    """Get the total size of a model's files from HuggingFace."""
    total = _get_repo_size_bytes(model_id)
    return total / (1024 ** 3) if total else 0.0


def save_model_choices(
    assistant: str,
    filter_model: str,
    vlm_model: str = "",
) -> None:
    """Persist model choices to the user config file.

    Args:
        assistant: HuggingFace model ID for the assistant (text) model.
        filter_model: HuggingFace model ID or ``"apple"`` for the filter model.
        vlm_model: HuggingFace model ID for the VLM model (optional).
    """
    from giva.config import save_llm_config

    save_llm_config(model=assistant, filter_model=filter_model, vlm_model=vlm_model)


def is_model_setup_complete() -> bool:
    """Check if the user has completed model setup.

    Verifies that:
    1. A user config file exists with an [llm] section
    2. The configured models are actually downloaded
       (Apple model ``"apple"`` counts as always available)
    """
    import tomllib

    from giva.llm.apple_adapter import is_apple_model

    config_path = Path("~/.config/giva/config.toml").expanduser()
    if not config_path.exists():
        log.info("is_model_setup_complete: config %s does not exist → False", config_path)
        return False
    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        llm = raw.get("llm", {})
        model = llm.get("model")
        filter_model = llm.get("filter_model")
        if not model or not filter_model:
            log.info("is_model_setup_complete: no model/filter in config → False")
            return False
        # Verify models are downloaded (Apple model needs no download)
        downloaded = get_downloaded_model_ids()
        assistant_ok = model in downloaded
        filter_ok = is_apple_model(filter_model) or filter_model in downloaded
        result = assistant_ok and filter_ok
        log.info(
            "is_model_setup_complete: model=%s filter=%s → %s",
            model, filter_model, result,
        )
        return result
    except Exception as e:
        log.warning("is_model_setup_complete: exception %s → False", e)
        return False


# --- VLM model discovery ---


_VLM_CACHE_KEY = "vlm_model_cache.json"

# Prompt for LLM-based VLM model recommendation
_VLM_RECOMMEND_PROMPT = """Pick the best MLX Vision-Language Model for browser automation on Apple Silicon.

Hardware: {chip}, {ram_gb}GB unified memory. Budget remaining after text models: {vlm_budget_gb:.1f}GB.

VLM CANDIDATES (all fit in your remaining budget):
{vlm_table}

Rules:
1. Pick the model that best balances visual understanding quality with size constraints.
2. Prefer Qwen2.5-VL family (best open VLM for structured output).
3. Prefer 4-bit quantization for efficiency.
4. Pick the largest model that fits comfortably in the remaining budget.
5. Avoid models smaller than 3B params (insufficient for browser task reasoning).

Respond with ONLY JSON:
{{"vlm_model": "exact_model_id", "reasoning": "why"}} /no_think"""


def list_mlx_vlm_models(
    cache_dir: Optional[Path] = None,
) -> list[dict]:
    """Query HuggingFace Hub for MLX vision-language models.

    Searches mlx-community for VLM models (image-text-to-text pipeline)
    and also searches by keyword for known VLM families.

    Returns a list of dicts with keys: model_id, size_gb, params, quant, downloads.
    Results are cached for 24h alongside text model cache.
    """
    base = cache_dir or Path("~/.local/share/giva").expanduser()
    cache_path = base / _VLM_CACHE_KEY
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    from huggingface_hub import HfApi

    api = HfApi()
    seen_ids: set[str] = set()
    models: list[dict] = []

    def _collect(raw_models):
        for m in raw_models:
            model_id = m.id
            if model_id in seen_ids:
                continue
            name_lower = model_id.lower()
            if any(k in name_lower for k in ("embedding", "reward", "reranker", "gguf")):
                continue
            params, quant = _parse_model_name(model_id)
            if not params:
                continue
            seen_ids.add(model_id)
            models.append({
                "model_id": model_id,
                "size_gb": _estimate_size_gb(params, quant),
                "params": params,
                "quant": quant,
                "downloads": m.downloads or 0,
            })

    # Search 1: image-text-to-text pipeline (official VLM tag)
    try:
        _collect(api.list_models(
            author="mlx-community",
            pipeline_tag="image-text-to-text",
            sort="downloads",
            limit=100,
        ))
    except Exception:
        pass

    # Search 2: keyword searches for known VLM families
    vlm_keywords = [
        "Qwen2.5-VL", "Qwen2-VL", "Qwen-VL", "InternVL",
        "Pixtral", "Llama-3.2-Vision", "Phi-3.5-vision",
        "Gemma-3-vision", "SmolVLM",
    ]
    for keyword in vlm_keywords:
        try:
            _collect(api.list_models(
                author="mlx-community",
                search=keyword,
                sort="downloads",
                limit=20,
            ))
        except Exception:
            pass

    # Enrich top models with actual file sizes
    by_size = sorted(models, key=lambda m: m["size_gb"], reverse=True)
    _enrich_sizes(api, by_size[:20])

    _save_cache(cache_path, models)
    return models


def recommend_vlm_model(
    hardware_info: dict,
    vlm_models: list[dict],
    assistant_size_gb: float = 0.0,
    filter_size_gb: float = 0.0,
    config=None,
) -> dict:
    """Recommend the best VLM model for the user's hardware.

    Takes into account the space already consumed by text models so the
    VLM model fits in remaining memory.

    Returns {"vlm_model": model_id, "reasoning": str}.
    Falls back to heuristic if LLM fails.
    """
    ram_gb = hardware_info.get("ram_gb", 8)
    max_size = max_model_size_gb(ram_gb)
    vlm_budget = max(max_size - assistant_size_gb - filter_size_gb, 2.0)

    # Filter to models that fit
    candidates = sorted(
        [m for m in vlm_models if m["size_gb"] <= vlm_budget],
        key=lambda m: m["size_gb"],
        reverse=True,
    )

    if not candidates:
        return {
            "vlm_model": "",
            "reasoning": "No VLM model fits in remaining memory budget.",
        }

    # Build table for the prompt
    vlm_lines = []
    for m in candidates[:15]:
        vlm_lines.append(
            f"- {m['model_id']}  size={m['size_gb']:.1f}GB  "
            f"params={m['params']}  quant={m['quant']}  downloads={m['downloads']}"
        )
    vlm_table = "\n".join(vlm_lines) if vlm_lines else "None found."

    # Try LLM recommendation
    try:
        from giva.llm.engine import manager

        model_id = DEFAULT_MODEL
        if config:
            model_id = config.llm.filter_model or DEFAULT_MODEL

        prompt = _VLM_RECOMMEND_PROMPT.format(
            chip=hardware_info.get("chip", "Unknown"),
            ram_gb=ram_gb,
            vlm_budget_gb=vlm_budget,
            vlm_table=vlm_table,
        )

        response = manager.generate(
            model_id,
            [
                {"role": "system", "content": "You are a system configuration assistant."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temp=0.1,
            top_p=0.95,
        )

        result = _parse_vlm_recommendation(response)
        if result:
            # Validate that model is in our candidate list
            valid_ids = {m["model_id"] for m in candidates}
            if result["vlm_model"] in valid_ids:
                return result
            log.warning(
                "LLM VLM recommendation not in candidates: %s", result["vlm_model"]
            )
    except Exception as e:
        log.warning("LLM VLM recommendation failed, using heuristic: %s", e)

    return _heuristic_vlm_recommendation(candidates)


def _parse_vlm_recommendation(response: str) -> Optional[dict]:
    """Parse the LLM's JSON VLM recommendation response."""
    json_match = re.search(r'\{[^{}]*"vlm_model"[^{}]*\}', response, re.DOTALL)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
        if "vlm_model" in data:
            return {
                "vlm_model": str(data["vlm_model"]),
                "reasoning": str(data.get("reasoning", "")),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _heuristic_vlm_recommendation(candidates: list[dict]) -> dict:
    """Fallback heuristic VLM recommendation.

    Prefers: Qwen2.5-VL family, 4-bit quant, larger size, Instruct variant.
    """
    def _score(m: dict) -> float:
        mid = m["model_id"].lower()
        s = 0.0
        # Prefer Qwen2.5-VL (best open VLM for structured tasks)
        if "qwen2.5-vl" in mid:
            s += 30
        elif "qwen2-vl" in mid or "qwen-vl" in mid:
            s += 20
        # Prefer larger models (better visual understanding)
        s += min(m["size_gb"] * 3, 30)
        # Prefer 4-bit for efficiency
        if m.get("quant", "") == "4bit":
            s += 10
        # Prefer Instruct variants
        if "instruct" in mid:
            s += 10
        # Penalize very small models
        param_match = re.match(r"(\d+(?:\.\d+)?)", m.get("params", "0"))
        if param_match and float(param_match.group(1)) < 3:
            s -= 20
        return s

    best = max(candidates, key=_score)
    return {
        "vlm_model": best["model_id"],
        "reasoning": (
            f"Selected {best['model_id']} ({best['size_gb']:.1f} GB) as the best "
            "VLM for browser automation within the remaining memory budget."
        ),
    }


# --- Internal helpers ---


def _cache_path(cache_dir: Optional[Path] = None) -> Path:
    """Path to the model list cache file."""
    base = cache_dir or Path("~/.local/share/giva").expanduser()
    return base / "model_cache.json"


def _load_cache(path: Path) -> Optional[list[dict]]:
    """Load cached model list if fresh enough."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("timestamp", 0) < _CACHE_TTL_SECONDS:
            return data.get("models", [])
    except Exception:
        pass
    return None


def _save_cache(path: Path, models: list[dict]) -> None:
    """Save model list to cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "timestamp": time.time(),
            "models": models,
        }))
    except Exception as e:
        log.warning("Could not save model cache: %s", e)


def _parse_model_name(model_id: str) -> tuple[str, str]:
    """Extract parameter count and quantization from model ID.

    Returns (params, quant) e.g. ("8B", "4bit") or ("", "") if unparsable.
    """
    name = model_id.split("/")[-1] if "/" in model_id else model_id

    # Quantization first (so we can exclude "8bit" region from param parsing)
    quant = "unknown"
    quant_patterns = [
        (r"(\d+)bit", lambda m: f"{m.group(1)}bit"),
        (r"MXFP(\d+)", lambda m: f"MXFP{m.group(1)}"),
        (r"qat-(\d+)bit", lambda m: f"{m.group(1)}bit"),
        (r"-Q(\d+)", lambda m: f"Q{m.group(1)}"),
    ]
    quant_span = None
    for pattern, fmt in quant_patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            quant = fmt(match)
            quant_span = match.span()
            break

    # Parameter count: look for "-8B-", "-30B-", "-0.6B-" etc.
    # Must be preceded by a dash and followed by a dash or end-of-string.
    # This avoids matching version numbers like "GLM-4.7" or quantization like "8bit".
    params = ""
    for m in re.finditer(r"(?<=-)(\d+(?:\.\d+)?)[Bb](?=-|$)", name):
        # Skip if this overlaps with the quantization match
        if quant_span and m.start() >= quant_span[0] and m.end() <= quant_span[1]:
            continue
        params = f"{m.group(1)}B"
        break

    # Fallback: try less strict pattern (no dash requirement) for edge cases
    if not params:
        for m in re.finditer(r"(?<![a-zA-Z.])(\d+(?:\.\d+)?)[Bb](?![a-zA-Z])", name):
            if quant_span and m.start() >= quant_span[0] and m.end() <= quant_span[1]:
                continue
            params = f"{m.group(1)}B"
            break

    return params, quant


def _estimate_size_gb(params: str, quant: str) -> float:
    """Estimate model size from parameter count and quantization.

    Rule of thumb: 4-bit ≈ 0.5 GB per billion params, 8-bit ≈ 1 GB/B.
    """
    # Parse params like "8B" → 8.0, "0.6B" → 0.6
    match = re.match(r"(\d+(?:\.\d+)?)", params)
    if not match:
        return 0.0
    param_billions = float(match.group(1))

    # Quantization multiplier
    if "4" in quant:
        return round(param_billions * 0.55, 1)  # ~0.55 GB/B for 4-bit
    elif "8" in quant:
        return round(param_billions * 1.05, 1)  # ~1.05 GB/B for 8-bit
    else:
        return round(param_billions * 0.55, 1)  # Default to 4-bit estimate


def _enrich_sizes(api, models: list[dict]) -> None:
    """Replace estimated sizes with actual sizes from HuggingFace for top models."""
    for m in models:
        try:
            files = list(api.list_repo_tree(m["model_id"]))
            total = sum(
                f.size for f in files
                if hasattr(f, "size") and f.size
                and f.rfilename.endswith((".safetensors", ".bin", ".gguf"))
            )
            if total > 0:
                m["size_gb"] = round(total / (1024 ** 3), 1)
        except Exception:
            pass  # Keep the estimate


def _get_repo_size_bytes(model_id: str) -> int:
    """Get total model file size in bytes from HuggingFace."""
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        files = list(api.list_repo_tree(model_id))
        return sum(
            f.size for f in files
            if hasattr(f, "size") and f.size
            and f.rfilename.endswith((".safetensors", ".bin", ".gguf"))
        )
    except Exception:
        return 0


def _parse_keyword_list(response: str) -> list[str]:
    """Parse LLM response containing a JSON array of search keywords."""
    # Find a JSON array in the response
    match = re.search(r'\[([^\]]+)\]', response)
    if not match:
        return []
    try:
        raw = json.loads(match.group())
        if isinstance(raw, list):
            return [str(k).strip() for k in raw if isinstance(k, str) and k.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _parse_recommendation(response: str) -> Optional[dict]:
    """Parse the LLM's JSON recommendation response."""
    # Extract JSON from potential markdown fences or extra text
    json_match = re.search(r'\{[^{}]*"assistant"[^{}]*\}', response, re.DOTALL)
    if not json_match:
        return None

    try:
        data = json.loads(json_match.group())
        if "assistant" in data and "filter" in data:
            return {
                "assistant": str(data["assistant"]),
                "filter": str(data["filter"]),
                "reasoning": str(data.get("reasoning", "")),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _validate_recommendation(rec: dict, models: list[dict], max_size: float) -> bool:
    """Validate that an LLM recommendation is reasonable.

    Rejects recommendations where the assistant model is much smaller than what fits.
    """
    model_map = {m["model_id"]: m for m in models}
    assistant = model_map.get(rec["assistant"])
    filter_m = model_map.get(rec["filter"])

    # Both models must exist in the compatible list
    if not assistant or not filter_m:
        return False

    # Combined size must fit in budget
    if assistant["size_gb"] + filter_m["size_gb"] > max_size:
        return False

    # The assistant should use at least 20% of the budget (avoid absurdly small picks)
    # For a 128GB Mac (96GB budget), this means assistant should be >= ~19GB
    if assistant["size_gb"] < max_size * 0.2:
        return False

    return True


def _heuristic_recommendation(models: list[dict], max_size: float) -> dict:
    """Fallback heuristic recommendation when LLM is unavailable.

    Scoring prefers: large size (uses available RAM), MoE architecture (fast inference),
    Instruct fine-tune (better at chat), Qwen3 family (tested with this app), 4-bit quant.
    """
    filter_budget = 5.0  # GB reserved for the filter model

    def _score(m: dict) -> float:
        """Score a model for assistant suitability. Higher = better."""
        mid = m["model_id"].lower()
        s = 0.0
        # Base: reward using more of the available RAM (0-50 points)
        s += (m["size_gb"] / max(max_size - filter_budget, 1)) * 50
        # MoE models (A3B, A22B, etc.) are much faster at inference
        if re.search(r"-a\d+b", mid):
            s += 25
        # Reasoning/thinking models are more capable
        if "thinking" in mid or "r1" in mid:
            s += 20
        # Instruct/chat fine-tune
        if "instruct" in mid:
            s += 15
        # Qwen3 family (well-tested with this app, strong reasoning)
        if "qwen3" in mid:
            s += 15
        # Prefer 4-bit for efficiency
        if m.get("quant", "") == "4bit":
            s += 5
        # Penalize coder variants (too specialized for general assistant)
        if "coder" in mid:
            s -= 20
        # Penalize old model families
        if "llama-2" in mid or "qwen2.5" in mid.replace(".", "") and "qwen3" not in mid:
            s -= 5
        return s

    # Filter to models that leave room for a filter model
    candidates = [m for m in models if m["size_gb"] <= max_size - filter_budget]
    if not candidates:
        candidates = models[:5] if models else []

    # Pick best assistant
    assistant = DEFAULT_MODEL
    if candidates:
        best = max(candidates, key=_score)
        assistant = best["model_id"]

    # Filter model: smallest Qwen3 4-bit, or just smallest overall
    filter_m = DEFAULT_MODEL
    sorted_small = sorted(models, key=lambda m: m["size_gb"])
    # Prefer small Qwen3 models for filter
    for m in sorted_small:
        if m["size_gb"] <= 5.0 and m["model_id"] != assistant and "qwen3" in m["model_id"].lower():
            filter_m = m["model_id"]
            break
    else:
        for m in sorted_small:
            if m["size_gb"] <= 5.0 and m["model_id"] != assistant:
                filter_m = m["model_id"]
                break

    return {
        "assistant": assistant,
        "filter": filter_m,
        "reasoning": (
            f"Selected the best model for {max_size:.0f}GB budget as assistant "
            "(prioritizing size, MoE architecture, and Instruct fine-tune), "
            "and a small Qwen3 model for fast email filtering."
        ),
    }
