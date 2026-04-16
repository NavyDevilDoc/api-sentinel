"""Unit tests for sentinel.config — config loading and pydantic validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sentinel.config import SentinelConfig, load_config


class TestLoadConfig:
    """Tests for the load_config function."""

    def test_valid_config_loads(self, tmp_config_file: Path) -> None:
        """A well-formed YAML file produces a valid SentinelConfig."""
        config = load_config(tmp_config_file)
        assert config.meta.project == "test-api"
        assert config.meta.base_url == "https://api.test.com"
        assert len(config.endpoints) == 2

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        """An empty YAML file raises ValueError."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Config file is empty"):
            load_config(empty)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """Malformed YAML content raises an error."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("meta:\n  project: [unclosed", encoding="utf-8")
        with pytest.raises(Exception):
            load_config(bad)


class TestSentinelConfig:
    """Tests for the SentinelConfig pydantic model."""

    def test_missing_required_field_raises(self, sample_config_dict: dict) -> None:
        """Omitting meta.base_url raises ValidationError."""
        del sample_config_dict["meta"]["base_url"]
        with pytest.raises(ValidationError, match="base_url"):
            SentinelConfig.model_validate(sample_config_dict)

    def test_trailing_slash_stripped(self) -> None:
        """Trailing slash on base_url is automatically removed."""
        config = SentinelConfig.model_validate({
            "meta": {
                "project": "test",
                "base_url": "https://example.com/",
            },
            "auth": {"token_primary": "TOK"},
            "endpoints": [],
            "checks": {"authorization": {"enabled": False}},
        })
        assert config.meta.base_url == "https://example.com"

    def test_bola_requires_secondary_token(self) -> None:
        """Enabling authorization checks without token_secondary raises."""
        with pytest.raises(
            ValidationError,
            match="Authorization.*BOLA.*require.*token_secondary",
        ):
            SentinelConfig.model_validate({
                "meta": {"project": "test", "base_url": "https://example.com"},
                "auth": {"token_primary": "TOK"},
                "endpoints": [],
                "checks": {"authorization": {"enabled": True}},
            })

    def test_bola_disabled_without_secondary_token_ok(self) -> None:
        """Disabling authorization checks works without token_secondary."""
        config = SentinelConfig.model_validate({
            "meta": {"project": "test", "base_url": "https://example.com"},
            "auth": {"token_primary": "TOK"},
            "endpoints": [],
            "checks": {"authorization": {"enabled": False}},
        })
        assert config.checks.authorization.enabled is False

    def test_defaults_applied(self) -> None:
        """Minimal config gets sensible defaults."""
        config = SentinelConfig.model_validate({
            "meta": {"project": "test", "base_url": "https://example.com"},
            "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
            "endpoints": [],
        })
        assert config.meta.timeout_seconds == 10.0
        assert config.checks.transport.enabled is True
        assert config.checks.rate_limit.request_burst == 20

    def test_mixed_test_ids(self, sample_config_dict: dict) -> None:
        """EndpointConfig accepts both int and string test_ids."""
        sample_config_dict["endpoints"][0]["test_ids"] = [1, "abc", 42]
        config = SentinelConfig.model_validate(sample_config_dict)
        assert config.endpoints[0].test_ids == [1, "abc", 42]
