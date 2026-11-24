"""`almanac config set` / `config update` (bug D): typed values, clear errors."""

import pytest
import yaml
from click.testing import CliRunner

import almanac
from almanac.cli import main
from almanac.config import Config, ConfigValueError, coerce_value, set_config_value


@pytest.fixture
def scratch_config(tmp_path, monkeypatch):
    """A fresh Config in memory and a scratch HOME (never the real config file)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config = Config()
    monkeypatch.setattr(almanac, "config", config)
    return config, tmp_path / ".almanac" / "config.yaml"


def run(args):
    return CliRunner().invoke(main, args)


def test_set_stores_typed_values(scratch_config):
    config, path = scratch_config
    assert run(["config", "set", "logging_level", "10"]).exit_code == 0
    assert run(["config", "set", "database_retry_backoff", "0.5"]).exit_code == 0
    assert run(["config", "set", "sdssdb.port", "63333"]).exit_code == 0
    assert run(["config", "set", "display_field_names", "exposure,image_type"]).exit_code == 0
    saved = yaml.safe_load(path.read_text())
    assert saved["logging_level"] == 10 and isinstance(saved["logging_level"], int)
    assert saved["database_retry_backoff"] == 0.5
    assert saved["sdssdb"]["port"] == 63333
    assert saved["display_field_names"] == ["exposure", "image_type"]
    assert config.logging_level == 10


def test_invalid_value_is_refused(scratch_config):
    config, path = scratch_config
    result = run(["config", "set", "logging_level", "debug"])
    assert result.exit_code == 1
    assert "expected an integer" in result.output
    assert config.logging_level == 20 and not path.exists()


def test_unknown_and_group_keys(scratch_config):
    assert "No configuration key 'nope'" in run(["config", "set", "nope", "1"]).output
    assert "configuration class" in run(["config", "set", "sdssdb", "1"]).output
    assert "within 'sdssdb'" in run(["config", "set", "sdssdb.nope", "1"]).output


def test_update_alias_works(scratch_config):
    config, path = scratch_config
    result = run(["config", "update", "catalog_query_max_workers", "2"])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(path.read_text())["catalog_query_max_workers"] == 2


def test_coerce_value():
    from typing import List
    assert coerce_value(int, " 7 ") == 7
    assert coerce_value(float, "1e-3") == 0.001
    assert coerce_value(str, "x") == "x"
    assert coerce_value(bool, "yes") is True
    assert coerce_value(List[str], "[a, b]") == ["a", "b"]
    with pytest.raises(ConfigValueError):
        coerce_value(int, "3.5")
    with pytest.raises(ConfigValueError):
        set_config_value(Config(), "database_retry_attempts", "many")
