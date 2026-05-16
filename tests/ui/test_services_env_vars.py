"""Phase UI-4 — service-layer tests for the env var lister."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sentinel.ui.services.env_vars import (  # noqa: E402
    DEFAULT_PREFIX,
    list_env_var_names,
)


class TestListEnvVarNames:
    def test_default_prefix_returns_only_sentinel_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SENTINEL_UI4_FOO", "value-must-not-leak")
        monkeypatch.setenv("OTHER_UI4_BAR", "value-must-not-leak")
        names = list_env_var_names()
        assert "SENTINEL_UI4_FOO" in names
        assert "OTHER_UI4_BAR" not in names

    def test_results_are_sorted_alphabetically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UI4_TEST_ZULU", "v")
        monkeypatch.setenv("UI4_TEST_ALPHA", "v")
        monkeypatch.setenv("UI4_TEST_MIKE", "v")
        names = list_env_var_names(prefix="UI4_TEST_")
        assert names == ["UI4_TEST_ALPHA", "UI4_TEST_MIKE", "UI4_TEST_ZULU"]

    def test_custom_prefix_filters_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APITOOL_FOO", "v")
        monkeypatch.setenv("SENTINEL_BAR", "v")
        names = list_env_var_names(prefix="APITOOL_")
        assert "APITOOL_FOO" in names
        assert "SENTINEL_BAR" not in names

    def test_empty_prefix_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Footgun guard: empty prefix must not return the whole environment."""
        monkeypatch.setenv("SENTINEL_UI4_KNOWN", "v")
        monkeypatch.setenv("UNRELATED_UI4_VAR", "v")
        names = list_env_var_names(prefix="")
        assert "SENTINEL_UI4_KNOWN" in names
        assert "UNRELATED_UI4_VAR" not in names

    def test_no_matches_returns_empty_list(self) -> None:
        names = list_env_var_names(prefix="ZZUI4ZZ_NONEXISTENT_PREFIX_")
        assert names == []

    def test_default_prefix_constant_is_sentinel(self) -> None:
        assert DEFAULT_PREFIX == "SENTINEL_"
