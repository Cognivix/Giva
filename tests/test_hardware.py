"""Tests for Mac hardware detection."""

from giva.hardware import get_hardware_info, max_model_size_gb


def test_get_hardware_info_returns_expected_keys():
    """Should return dict with chip, ram_gb, gpu_cores."""
    info = get_hardware_info()
    assert "chip" in info
    assert "ram_gb" in info
    assert "gpu_cores" in info
    assert isinstance(info["chip"], str)
    assert isinstance(info["ram_gb"], int)
    assert isinstance(info["gpu_cores"], int)


def test_hardware_info_ram_positive():
    """RAM should be at least 8GB on any M-series Mac."""
    info = get_hardware_info()
    assert info["ram_gb"] >= 8


def test_max_model_size_128gb():
    """128GB RAM → ~96GB max model size."""
    assert max_model_size_gb(128) == 96.0


def test_max_model_size_8gb():
    """8GB RAM → ~6GB max model size."""
    assert max_model_size_gb(8) == 6.0


def test_max_model_size_24gb():
    """24GB RAM → ~18GB max model size."""
    assert max_model_size_gb(24) == 18.0
