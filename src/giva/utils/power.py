"""System power and thermal state monitoring for resource-aware scheduling.

Provides cached readings of battery status, thermal state, and memory pressure
so the scheduler and model manager can adapt to system conditions without
burning subprocess calls on every check.

macOS-only: uses ``pmset``, ``vm_stat``, and optionally PyObjC for thermal state.
"""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Thermal state constants (match NSProcessInfo.ThermalState)
THERMAL_NOMINAL = 0
THERMAL_FAIR = 1
THERMAL_SERIOUS = 2
THERMAL_CRITICAL = 3

# Cache TTL in seconds
_CACHE_TTL = 30.0


@dataclass(frozen=True)
class PowerState:
    """Snapshot of the system's power and resource state."""

    on_battery: bool
    battery_percent: int | None  # None on desktops without a battery
    thermal_state: int  # 0=nominal, 1=fair, 2=serious, 3=critical
    memory_pressure_pct: float  # 0.0–100.0 (percentage of RAM in use)
    timestamp: float  # time.monotonic() when this reading was taken


# Module-level cache
_cached_state: PowerState | None = None
_cached_at: float = 0.0


def get_power_state() -> PowerState:
    """Return the current power state, refreshing if cache is stale."""
    global _cached_state, _cached_at
    now = time.monotonic()
    if _cached_state is not None and (now - _cached_at) < _CACHE_TTL:
        return _cached_state

    state = _read_power_state()
    _cached_state = state
    _cached_at = now
    return state


def _read_power_state() -> PowerState:
    """Read all power/resource sensors and return a fresh PowerState."""
    on_battery, battery_pct = _read_battery()
    thermal = _read_thermal_state()
    mem_pct = _read_memory_pressure()
    return PowerState(
        on_battery=on_battery,
        battery_percent=battery_pct,
        thermal_state=thermal,
        memory_pressure_pct=mem_pct,
        timestamp=time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Battery — via pmset
# ---------------------------------------------------------------------------

def _read_battery() -> tuple[bool, int | None]:
    """Return (on_battery, battery_percent) using ``pmset -g ps``.

    Returns (False, None) on desktops without a battery or on error.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/pmset", "-g", "ps"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False, None

        output = result.stdout
        on_battery = "Battery Power" in output

        # Parse percentage from lines like: "-InternalBattery-0 (id=...)  85%; charging;"
        match = re.search(r"(\d+)%", output)
        battery_pct = int(match.group(1)) if match else None

        return on_battery, battery_pct
    except Exception as e:
        log.debug("Battery read failed: %s", e)
        return False, None


# ---------------------------------------------------------------------------
# Thermal state — via PyObjC (optional)
# ---------------------------------------------------------------------------

_thermal_available: bool | None = None


def _read_thermal_state() -> int:
    """Return macOS thermal state (0–3) via NSProcessInfo.

    Falls back to 0 (nominal) if PyObjC is not available.
    """
    global _thermal_available
    if _thermal_available is False:
        return THERMAL_NOMINAL

    try:
        from Foundation import NSProcessInfo
        state = NSProcessInfo.processInfo().thermalState()
        _thermal_available = True
        return int(state)
    except ImportError:
        if _thermal_available is None:
            log.info("PyObjC not available — thermal monitoring disabled")
            _thermal_available = False
        return THERMAL_NOMINAL
    except Exception as e:
        log.debug("Thermal state read failed: %s", e)
        return THERMAL_NOMINAL


# ---------------------------------------------------------------------------
# Memory pressure — via vm_stat (no psutil dependency)
# ---------------------------------------------------------------------------

def _read_memory_pressure() -> float:
    """Return memory usage percentage (0–100) using ``vm_stat`` + ``sysctl``.

    Returns 0.0 on error.
    """
    try:
        # Total physical memory
        hw_result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if hw_result.returncode != 0:
            return 0.0
        total_bytes = int(hw_result.stdout.strip())

        # Page statistics
        vm_result = subprocess.run(
            ["/usr/bin/vm_stat"],
            capture_output=True, text=True, timeout=5,
        )
        if vm_result.returncode != 0:
            return 0.0

        # Parse page size and page counts
        page_size = 16384  # default on Apple Silicon
        ps_match = re.search(r"page size of (\d+) bytes", vm_result.stdout)
        if ps_match:
            page_size = int(ps_match.group(1))

        def _parse_pages(label: str) -> int:
            m = re.search(rf"{label}:\s+(\d+)", vm_result.stdout)
            return int(m.group(1)) if m else 0

        free = _parse_pages("Pages free")
        inactive = _parse_pages("Pages inactive")
        speculative = _parse_pages("Pages speculative")

        available_bytes = (free + inactive + speculative) * page_size
        used_pct = (1.0 - available_bytes / total_bytes) * 100.0
        return max(0.0, min(100.0, used_pct))
    except Exception as e:
        log.debug("Memory pressure read failed: %s", e)
        return 0.0


# ---------------------------------------------------------------------------
# Thread QoS — via ctypes (macOS-only)
# ---------------------------------------------------------------------------

_QOS_CLASS_BACKGROUND = 0x09
_qos_initialized = False
_qos_fn = None


def set_thread_qos_background() -> None:
    """Set the current thread to QOS_CLASS_BACKGROUND (lowest priority).

    No-op on non-macOS platforms or if the ctypes call fails.
    Must be called from within the target thread.
    """
    if sys.platform != "darwin":
        return

    global _qos_initialized, _qos_fn
    if not _qos_initialized:
        _qos_initialized = True
        try:
            libpthread = ctypes.CDLL("/usr/lib/system/libpthread.dylib", use_errno=True)
            fn = libpthread.pthread_set_qos_class_self_np
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_uint, ctypes.c_int]
            _qos_fn = fn
        except Exception as e:
            log.debug("Thread QoS setup failed: %s", e)
            return

    if _qos_fn is not None:
        result = _qos_fn(_QOS_CLASS_BACKGROUND, 0)
        if result != 0:
            log.debug("pthread_set_qos_class_self_np returned %d", result)
