"""Phase UI-3 — tests for the read-only /config viewer route."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sentinel.ui.server import create_app  # noqa: E402


_GOOD_YAML = (
    "meta:\n"
    "  project: test-project\n"
    "  base_url: https://api.example.com\n"
    "  timeout_seconds: 8\n"
    "auth:\n"
    "  token_primary: SENTINEL_TOKEN_PRIMARY\n"
    "  token_secondary: SENTINEL_TOKEN_SECONDARY\n"
    "endpoints:\n"
    "  - path: /resource/{id}\n"
    "    method: GET\n"
    "    requires_auth: true\n"
    "    test_ids: [1, 2]\n"
    "    owned_by: token_primary\n"
    "  - path: /auth/login\n"
    "    method: POST\n"
    "    requires_auth: false\n"
    "    rate_limit_sensitive: true\n"
    "checks:\n"
    "  authorization:\n"
    "    enabled: true\n"
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def good_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Write a valid config to a temp dir and chdir there."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "sentinel_config.yaml"
    path.write_text(_GOOD_YAML, encoding="utf-8")
    return path


class TestEmptyState:
    def test_no_config_file_renders_empty_state(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.get("/config")
        assert response.status_code == 200
        assert "No config loaded" in response.text
        assert "sentinel_config.yaml" in response.text


class TestValidConfig:
    def test_renders_meta(
        self, client: TestClient, good_config: Path
    ) -> None:
        response = client.get("/config")
        assert response.status_code == 200
        assert "test-project" in response.text
        assert "https://api.example.com" in response.text

    def test_renders_endpoints(
        self, client: TestClient, good_config: Path
    ) -> None:
        response = client.get("/config")
        # Path templates may be HTML-escaped, so check method + slug.
        assert "/resource" in response.text
        assert "/auth/login" in response.text
        assert "GET" in response.text
        assert "POST" in response.text

    def test_renders_token_env_var_names(
        self, client: TestClient, good_config: Path
    ) -> None:
        response = client.get("/config")
        assert "SENTINEL_TOKEN_PRIMARY" in response.text
        assert "SENTINEL_TOKEN_SECONDARY" in response.text

    def test_unresolved_token_shows_not_set(
        self,
        client: TestClient,
        good_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SENTINEL_TOKEN_PRIMARY", raising=False)
        response = client.get("/config")
        assert "(not set)" in response.text


class TestPathOverride:
    def test_absolute_path_works(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        other = tmp_path / "elsewhere.yaml"
        other.write_text(
            "meta:\n"
            "  project: explicit-via-path\n"
            "  base_url: https://api.other.com\n"
            "auth:\n  token_primary: SENTINEL_TOK\n"
            "endpoints: []\n"
            "checks:\n  authorization:\n    enabled: false\n",
            encoding="utf-8",
        )
        response = client.get(f"/config?path={other}")
        assert response.status_code == 200
        assert "explicit-via-path" in response.text

    def test_path_traversal_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.get("/config?path=../escape.yaml")
        assert response.status_code == 400
        body_lower = response.text.lower()
        assert "outside" in body_lower or "working directory" in body_lower
