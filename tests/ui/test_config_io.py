"""Phase UI-3 — service-layer tests for the config I/O wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from sentinel.ui.services.config_io import (  # noqa: E402
    EnvVarStatus,
    env_var_status,
    load_config_for_viewer,
    resolve_config_path,
)


# ---------------------------------------------------------------------------
# resolve_config_path
# ---------------------------------------------------------------------------


class TestResolveConfigPath:
    def test_none_returns_default_in_cwd(self, tmp_path: Path) -> None:
        result = resolve_config_path(None, cwd=tmp_path)
        assert result == tmp_path / "sentinel_config.yaml"

    def test_empty_string_returns_default(self, tmp_path: Path) -> None:
        result = resolve_config_path("", cwd=tmp_path)
        assert result == tmp_path / "sentinel_config.yaml"

    def test_relative_path_under_cwd_is_allowed(self, tmp_path: Path) -> None:
        result = resolve_config_path("configs/dev.yaml", cwd=tmp_path)
        assert result == (tmp_path / "configs" / "dev.yaml").resolve()

    def test_relative_path_escaping_cwd_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside the working"):
            resolve_config_path("../escape.yaml", cwd=tmp_path)

    def test_absolute_path_is_accepted(self, tmp_path: Path) -> None:
        absolute = tmp_path.parent / "elsewhere.yaml"
        result = resolve_config_path(str(absolute), cwd=tmp_path)
        assert result == absolute


# ---------------------------------------------------------------------------
# load_config_for_viewer
# ---------------------------------------------------------------------------


_GOOD_YAML = (
    "meta:\n"
    "  project: test-project\n"
    "  base_url: https://api.example.com\n"
    "auth:\n"
    "  token_primary: SENTINEL_TOKEN_A\n"
    "endpoints:\n"
    "  - path: /resource\n"
    "    method: GET\n"
    "checks:\n"
    "  authorization:\n"
    "    enabled: false\n"
)


class TestLoadConfigForViewer:
    def test_missing_file_returns_error_result(self, tmp_path: Path) -> None:
        result = load_config_for_viewer(tmp_path / "nope.yaml")
        assert result.config is None
        assert result.loaded is False
        assert "No config file found" in result.error

    def test_invalid_yaml_returns_error_result(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("meta: {invalid: : yaml\n", encoding="utf-8")
        result = load_config_for_viewer(bad)
        assert result.loaded is False
        assert result.error is not None

    def test_validation_error_returns_error_result(self, tmp_path: Path) -> None:
        bad = tmp_path / "incomplete.yaml"
        bad.write_text("meta:\n  project: foo\n", encoding="utf-8")
        result = load_config_for_viewer(bad)
        assert result.loaded is False
        assert result.error is not None

    def test_valid_config_loads_successfully(self, tmp_path: Path) -> None:
        good = tmp_path / "good.yaml"
        good.write_text(_GOOD_YAML, encoding="utf-8")
        result = load_config_for_viewer(good)
        assert result.loaded is True
        assert result.config.meta.project == "test-project"


# ---------------------------------------------------------------------------
# env_var_status — value-isolation invariant
# ---------------------------------------------------------------------------


class TestEnvVarStatus:
    def test_none_returns_none(self) -> None:
        assert env_var_status(None) is None

    def test_set_var_is_marked_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_VAR_PRESENT", "this-is-a-secret-value")
        status = env_var_status("TEST_VAR_PRESENT")
        assert isinstance(status, EnvVarStatus)
        assert status.name == "TEST_VAR_PRESENT"
        assert status.is_set is True
        # Invariant: the value must never appear in the dataclass — not in
        # repr, not in fields, not in display.
        assert "this-is-a-secret-value" not in repr(status)
        assert "this-is-a-secret-value" not in status.display

    def test_unset_var_is_marked_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TEST_VAR_ABSENT", raising=False)
        status = env_var_status("TEST_VAR_ABSENT")
        assert status.is_set is False
        assert "(not set)" in status.display
