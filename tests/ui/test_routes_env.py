"""Phase UI-4 — tests for the /env-vars picker endpoint."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sentinel.ui.server import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestEnvVarOptions:
    def test_returns_200_html(self, client: TestClient) -> None:
        response = client.get("/env-vars")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_includes_matching_env_vars_as_options(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SENTINEL_UI4_TEST_TOKEN", "value-must-not-leak")
        response = client.get("/env-vars")
        assert 'value="SENTINEL_UI4_TEST_TOKEN"' in response.text
        assert "SENTINEL_UI4_TEST_TOKEN" in response.text

    def test_does_NOT_include_env_var_values(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Critical: only names go to the wire, never values."""
        canary = "ZZZ-route-env-picker-leak-canary-ZZZ"
        monkeypatch.setenv("SENTINEL_UI4_LEAK_TEST", canary)
        response = client.get("/env-vars")
        assert canary not in response.text

    def test_excludes_non_matching_env_vars(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNRELATED_UI4_VAR_X", "v")
        response = client.get("/env-vars")
        assert "UNRELATED_UI4_VAR_X" not in response.text

    def test_custom_prefix_query_param(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UI4_CUSTOM_FOO", "v")
        response = client.get("/env-vars?prefix=UI4_CUSTOM_")
        assert 'value="UI4_CUSTOM_FOO"' in response.text

    def test_selected_query_param_marks_matching_option(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SENTINEL_UI4_TOKEN_PRIMARY", "v")
        monkeypatch.setenv("SENTINEL_UI4_TOKEN_SECONDARY", "v")
        response = client.get(
            "/env-vars?selected=SENTINEL_UI4_TOKEN_PRIMARY"
        )
        assert (
            'value="SENTINEL_UI4_TOKEN_PRIMARY" selected' in response.text
        )
        # The unselected one must not also be marked selected
        assert (
            'value="SENTINEL_UI4_TOKEN_SECONDARY" selected'
            not in response.text
        )

    def test_no_matching_vars_renders_disabled_help_message(
        self, client: TestClient
    ) -> None:
        """Use a prefix that definitely matches nothing."""
        response = client.get(
            "/env-vars?prefix=ZZUI4ZZ_NONEXISTENT_PREFIX_"
        )
        assert response.status_code == 200
        assert "disabled" in response.text
        assert "ZZUI4ZZ_NONEXISTENT_PREFIX_" in response.text
        # Must NOT render a real <option value=""> that could submit empty
        # without disabling it
        assert 'value="" disabled' in response.text

    def test_xss_safe_against_malicious_prefix(
        self, client: TestClient
    ) -> None:
        """The prefix is reflected in the empty-state help text. It must be
        escaped — Jinja autoescape handles this, but verify explicitly."""
        response = client.get("/env-vars?prefix=<script>alert(1)</script>")
        # The literal <script> tag must not appear in the rendered HTML
        assert "<script>alert(1)</script>" not in response.text
        # An escaped form should appear instead
        assert "&lt;script&gt;" in response.text
