"""Mac hardware detection for model sizing."""

from __future__ import annotations

import logging
import re
import subprocess

log = logging.getLogger(__name__)


def get_hardware_info() -> dict:
    """Detect Mac hardware: chip name, total RAM in GB, GPU core count.

    Returns dict with keys: chip, ram_gb, gpu_cores.
    Gracefully returns defaults on any detection failure.
    """
    return {
        "chip": _detect_chip(),
        "ram_gb": _detect_ram_gb(),
        "gpu_cores": _detect_gpu_cores(),
    }


def max_model_size_gb(ram_gb: int) -> float:
    """Conservative max model size that fits in unified memory.

    Reserves ~25% for OS + apps + both models loaded simultaneously.
    Returns the max size in GB for a single model.
    """
    return round(ram_gb * 0.75, 1)


def _detect_chip() -> str:
    """Detect Apple Silicon chip name (e.g. 'Apple M4 Max')."""
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "Unknown"


def _detect_ram_gb() -> int:
    """Detect total RAM in GB via sysctl."""
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            bytes_total = int(result.stdout.strip())
            return bytes_total // (1024 ** 3)
    except Exception:
        pass
    return 8  # Safe minimum for any M-series Mac


def _detect_gpu_cores() -> int:
    """Detect GPU core count from system_profiler."""
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            # Look for "Total Number of Cores: 40" or similar
            match = re.search(r"Total Number of Cores:\s*(\d+)", result.stdout)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return 0
