"""Tests for configuration loading."""

from giva.config import load_config, GivaConfig


def test_default_config():
    config = load_config()
    assert isinstance(config, GivaConfig)
    assert config.log_level == "INFO"
    assert config.mail.batch_size == 50
    assert "Qwen" in config.llm.model
    assert "Qwen" in config.llm.filter_model
    assert config.llm.filter_model != config.llm.model  # Different models
    # Filter model should be a small model (<=8B params)
    filter_name = config.llm.filter_model.lower()
    assert any(s in filter_name for s in ["0.6b", "1b", "3b", "4b", "8b"])
    assert config.llm.max_tokens == 2048
    assert config.calendar.sync_window_future_days == 30
