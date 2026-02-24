"""Extended tests for configuration: env overrides, merging, serialization."""

import os
from pathlib import Path
from unittest.mock import patch

from giva.config import (
    GivaConfig,
    _apply_env,
    _deep_merge,
    _to_bool,
    _toml_value,
    _write_toml,
    load_config,
    save_llm_config,
)


class TestToBool:

    def test_true_bool(self):
        assert _to_bool(True) is True

    def test_false_bool(self):
        assert _to_bool(False) is False

    def test_string_true(self):
        assert _to_bool("true") is True
        assert _to_bool("True") is True
        assert _to_bool("TRUE") is True

    def test_string_one(self):
        assert _to_bool("1") is True

    def test_string_yes(self):
        assert _to_bool("yes") is True
        assert _to_bool("YES") is True

    def test_string_false(self):
        assert _to_bool("false") is False
        assert _to_bool("no") is False
        assert _to_bool("0") is False
        assert _to_bool("") is False

    def test_integer(self):
        assert _to_bool(1) is True
        assert _to_bool(0) is False


class TestDeepMerge:

    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"section": {"key1": "val1", "key2": "val2"}}
        override = {"section": {"key2": "new", "key3": "val3"}}
        result = _deep_merge(base, override)
        assert result["section"] == {"key1": "val1", "key2": "new", "key3": "val3"}

    def test_override_replaces_non_dict_with_dict(self):
        base = {"key": "string_value"}
        override = {"key": {"nested": True}}
        result = _deep_merge(base, override)
        assert result["key"] == {"nested": True}

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_does_not_mutate_base(self):
        base = {"a": 1}
        _deep_merge(base, {"a": 2})
        assert base["a"] == 1


class TestApplyEnv:

    def test_overrides_llm_model(self):
        raw = {"llm": {"model": "original"}}
        with patch.dict(os.environ, {"GIVA_LLM_MODEL": "custom-model"}):
            result = _apply_env(raw)
        assert result["llm"]["model"] == "custom-model"

    def test_creates_section_if_missing(self):
        raw = {}
        with patch.dict(os.environ, {"GIVA_LOG_LEVEL": "DEBUG"}):
            result = _apply_env(raw)
        assert result["giva"]["log_level"] == "DEBUG"

    def test_multiple_overrides(self):
        raw = {"llm": {}, "mail": {}}
        env = {
            "GIVA_LLM_MODEL": "test-model",
            "GIVA_MAIL_BATCH_SIZE": "100",
            "GIVA_LOG_LEVEL": "WARNING",
        }
        with patch.dict(os.environ, env):
            result = _apply_env(raw)
        assert result["llm"]["model"] == "test-model"
        assert result["mail"]["batch_size"] == "100"
        assert result["giva"]["log_level"] == "WARNING"

    def test_no_env_vars_noop(self):
        raw = {"llm": {"model": "original"}}
        with patch.dict(os.environ, {}, clear=True):
            # Clear GIVA_ vars only
            for key in list(os.environ.keys()):
                if key.startswith("GIVA_"):
                    del os.environ[key]
            result = _apply_env(raw)
        assert result["llm"]["model"] == "original"


class TestTomlValue:

    def test_string(self):
        assert _toml_value("hello") == '"hello"'

    def test_bool_true(self):
        assert _toml_value(True) == "true"

    def test_bool_false(self):
        assert _toml_value(False) == "false"

    def test_integer(self):
        assert _toml_value(42) == "42"

    def test_float(self):
        assert _toml_value(0.7) == "0.7"

    def test_list(self):
        assert _toml_value(["a", "b"]) == '["a", "b"]'

    def test_empty_list(self):
        assert _toml_value([]) == "[]"


class TestWriteToml:

    def test_writes_flat_values(self, tmp_path):
        path = tmp_path / "test.toml"
        _write_toml(path, {"key": "value", "num": 42})
        content = path.read_text()
        assert 'key = "value"' in content
        assert "num = 42" in content

    def test_writes_sections(self, tmp_path):
        path = tmp_path / "test.toml"
        _write_toml(path, {"llm": {"model": "test", "temp": 0.7}})
        content = path.read_text()
        assert "[llm]" in content
        assert 'model = "test"' in content
        assert "temp = 0.7" in content


class TestSaveLlmConfig:

    def test_creates_config_file(self, tmp_path):
        config_path = tmp_path / "config" / "giva" / "config.toml"
        with patch("giva.config._USER_CONFIG", config_path):
            save_llm_config("model-a", "model-b")
        assert config_path.exists()
        content = config_path.read_text()
        assert 'model = "model-a"' in content
        assert 'filter_model = "model-b"' in content

    def test_preserves_existing_config(self, tmp_path):
        import tomllib

        config_path = tmp_path / "config.toml"
        config_path.write_text('[mail]\nmailboxes = ["INBOX"]\n')

        with patch("giva.config._USER_CONFIG", config_path):
            save_llm_config("new-model", "new-filter")

        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        assert raw["llm"]["model"] == "new-model"
        assert raw["mail"]["mailboxes"] == ["INBOX"]


class TestLoadConfigWithEnv:

    def test_env_overrides_llm_model(self):
        with patch.dict(os.environ, {"GIVA_LLM_MODEL": "env-model"}):
            config = load_config()
        assert config.llm.model == "env-model"

    def test_env_overrides_log_level(self):
        with patch.dict(os.environ, {"GIVA_LOG_LEVEL": "DEBUG"}):
            config = load_config()
        assert config.log_level == "DEBUG"

    def test_env_overrides_voice_enabled(self):
        with patch.dict(os.environ, {"GIVA_VOICE_ENABLED": "true"}):
            config = load_config()
        assert config.voice.enabled is True

    def test_env_overrides_batch_size(self):
        with patch.dict(os.environ, {"GIVA_MAIL_BATCH_SIZE": "200"}):
            config = load_config()
        assert config.mail.batch_size == 200


class TestGivaConfigDefaults:

    def test_db_path(self, tmp_path):
        config = GivaConfig(data_dir=tmp_path)
        assert config.db_path == tmp_path / "giva.db"

    def test_frozen(self):
        config = GivaConfig()
        with __import__("pytest").raises(AttributeError):
            config.log_level = "DEBUG"  # type: ignore[misc]
