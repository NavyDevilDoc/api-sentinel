"""Phase UI-5 — tests for the config editor routes.

Covers:
  - GET /config/edit  (load existing or render defaults)
  - POST /config/endpoints/add  (form-state-driven row insertion)
  - POST /config/endpoints/remove  (form-state-driven row deletion)
  - POST /config/save  (validate, write atomically, surface errors inline)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
    "endpoints:\n"
    "  - path: /resource/{id}\n"
    "    method: GET\n"
    "    test_ids: [1, 2]\n"
    "checks:\n"
    "  authorization:\n"
    "    enabled: false\n"
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def good_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "sentinel_config.yaml"
    path.write_text(_GOOD_YAML, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# GET /config/edit
# ---------------------------------------------------------------------------


class TestGetEditConfig:
    def test_returns_200_html(
        self, client: TestClient, good_config: Path
    ) -> None:
        response = client.get("/config/edit")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_loads_existing_config_into_form(
        self, client: TestClient, good_config: Path
    ) -> None:
        response = client.get("/config/edit")
        body = response.text
        # Field values from the existing config should appear in `value=` attrs
        assert 'value="test-project"' in body
        assert 'value="https://api.example.com"' in body
        assert "/resource/{id}" in body
        assert "SENTINEL_TOKEN_PRIMARY" in body

    def test_first_run_renders_defaults(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No config file → form renders with placeholders, not 500."""
        monkeypatch.chdir(tmp_path)
        response = client.get("/config/edit")
        assert response.status_code == 200
        assert "Edit Configuration" in response.text

    def test_path_traversal_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.get("/config/edit?path=../escape.yaml")
        assert response.status_code == 400

    def test_token_selects_render_options_server_side(
        self,
        client: TestClient,
        good_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: the token selects must render their <option> elements
        server-side, not via HTMX. The HTMX load-trigger pattern was found
        to inherit hx-target="#config-form-wrapper" from the parent form,
        causing the load response to obliterate the entire form."""
        monkeypatch.setenv("SENTINEL_REGRESSION_TOKEN_A", "v")
        monkeypatch.setenv("SENTINEL_REGRESSION_TOKEN_B", "v")
        response = client.get("/config/edit")
        body = response.text
        # The selects must NOT carry hx-get attributes (server-render only)
        assert 'id="auth_primary"' in body
        assert 'id="auth_secondary"' in body
        primary_block = body[body.find('id="auth_primary"'):body.find('id="auth_primary"') + 800]
        secondary_block = body[body.find('id="auth_secondary"'):body.find('id="auth_secondary"') + 800]
        assert "hx-get" not in primary_block
        assert "hx-get" not in secondary_block
        # Real env var names must appear as <option> values
        assert 'value="SENTINEL_REGRESSION_TOKEN_A"' in body
        assert 'value="SENTINEL_REGRESSION_TOKEN_B"' in body

    def test_path_picker_form_rendered(
        self, client: TestClient, good_config: Path
    ) -> None:
        """The path picker should be visible so the user can switch configs
        without editing the URL bar."""
        response = client.get("/config/edit")
        assert 'class="path-picker"' in response.text
        assert 'action="/config/edit"' in response.text


# ---------------------------------------------------------------------------
# POST /config/endpoints/add
# ---------------------------------------------------------------------------


class TestAddEndpoint:
    def test_appends_blank_endpoint_to_form_state(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # Send current form state with one existing endpoint
        form_data = {
            "meta.project": "p",
            "meta.base_url": "https://x",
            "meta.timeout_seconds": "10",
            "auth.token_primary": "SENTINEL_A",
            "endpoints[0].path": "/existing",
            "endpoints[0].method": "GET",
        }
        response = client.post(
            "/config/endpoints/add",
            data=form_data,
        )
        assert response.status_code == 200
        # Both the existing AND a new blank row should be in the response
        assert 'value="/existing"' in response.text
        assert 'name="endpoints[1].path"' in response.text


# ---------------------------------------------------------------------------
# POST /config/endpoints/remove
# ---------------------------------------------------------------------------


class TestRemoveEndpoint:
    def test_drops_indexed_endpoint(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        form_data = {
            "meta.project": "p",
            "meta.base_url": "https://x",
            "meta.timeout_seconds": "10",
            "auth.token_primary": "SENTINEL_A",
            "endpoints[0].path": "/keep",
            "endpoints[0].method": "GET",
            "endpoints[1].path": "/drop",
            "endpoints[1].method": "POST",
        }
        response = client.post(
            "/config/endpoints/remove?index=1",
            data=form_data,
        )
        assert response.status_code == 200
        assert "/keep" in response.text
        assert "/drop" not in response.text

    def test_out_of_range_index_is_silent(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        form_data = {
            "meta.project": "p",
            "meta.base_url": "https://x",
            "meta.timeout_seconds": "10",
            "auth.token_primary": "SENTINEL_A",
            "endpoints[0].path": "/keep",
            "endpoints[0].method": "GET",
        }
        response = client.post(
            "/config/endpoints/remove?index=99",
            data=form_data,
        )
        # Shouldn't crash, just leaves the list untouched.
        assert response.status_code == 200
        assert "/keep" in response.text


# ---------------------------------------------------------------------------
# POST /config/save
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_valid_form_writes_yaml(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "sentinel_config.yaml"

        form_data = {
            "meta.project": "saved-project",
            "meta.base_url": "https://api.saved.com",
            "meta.timeout_seconds": "15",
            "auth.token_primary": "SENTINEL_NEW",
            "auth.token_secondary": "",
            "endpoints[0].path": "/saved",
            "endpoints[0].method": "GET",
            "endpoints[0].requires_auth": "true",
            "endpoints[0].test_ids": "",
            "endpoints[0].owned_by": "",
            "endpoints[0].rate_limit_sensitive": "false",
            "checks.transport.enabled": "true",
            "checks.headers.enabled": "true",
            "checks.headers.required": "Strict-Transport-Security\nX-Content-Type-Options",
            "checks.headers.forbidden_leakage": "",
            "checks.auth.enabled": "true",
            "checks.authorization.enabled": "false",
            "checks.rate_limit.enabled": "true",
            "checks.rate_limit.request_burst": "10",
            "checks.rate_limit.burst_window_seconds": "5",
            "checks.input_handling.enabled": "true",
            "checks.input_handling.max_payload_kb": "1024",
            "backup": "false",
        }

        response = client.post("/config/save", data=form_data)
        assert response.status_code == 200
        assert "Saved to" in response.text

        # Verify YAML was actually written and round-trips
        assert target.exists()
        roundtrip = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert roundtrip["meta"]["project"] == "saved-project"
        assert roundtrip["checks"]["headers"]["required"] == [
            "Strict-Transport-Security",
            "X-Content-Type-Options",
        ]

    def test_invalid_form_returns_422_with_field_errors(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "sentinel_config.yaml"

        # `timeout_seconds: "not-a-number"` cannot be coerced to float
        form_data = {
            "meta.project": "incomplete",
            "meta.base_url": "https://x.com",
            "meta.timeout_seconds": "not-a-number",
            "auth.token_primary": "SENTINEL_A",
            "checks.authorization.enabled": "false",
        }

        response = client.post("/config/save", data=form_data)
        assert response.status_code == 422
        # Form should NOT have been written
        assert not target.exists()
        # An error indicator should appear somewhere in the response
        assert "Validation failed" in response.text or "field-error" in response.text

    def test_backup_query_writes_bak_file(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "sentinel_config.yaml"
        target.write_text("old_marker: yes\n", encoding="utf-8")

        form_data = {
            "meta.project": "p",
            "meta.base_url": "https://x.com",
            "meta.timeout_seconds": "10",
            "auth.token_primary": "SENTINEL_A",
            "checks.authorization.enabled": "false",
            "backup": "true",
        }

        response = client.post("/config/save", data=form_data)
        assert response.status_code == 200

        bak = tmp_path / "sentinel_config.yaml.bak"
        assert bak.exists()
        assert "old_marker" in bak.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Round-trip — load an existing config, save it back, content survives
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_load_edit_save_preserves_content(
        self,
        client: TestClient,
        good_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Load existing config, post it back unchanged, verify equivalence
        through pydantic. Comments are lost (documented limitation) but
        semantic content survives."""
        # Submit a form equivalent to the loaded config
        form_data = {
            "meta.project": "test-project",
            "meta.base_url": "https://api.example.com",
            "meta.timeout_seconds": "8",
            "auth.token_primary": "SENTINEL_TOKEN_PRIMARY",
            "auth.token_secondary": "",
            "endpoints[0].path": "/resource/{id}",
            "endpoints[0].method": "GET",
            "endpoints[0].requires_auth": "true",
            "endpoints[0].test_ids": "1, 2",
            "endpoints[0].owned_by": "",
            "endpoints[0].rate_limit_sensitive": "false",
            "checks.transport.enabled": "true",
            "checks.headers.enabled": "true",
            "checks.auth.enabled": "true",
            "checks.authorization.enabled": "false",
            "checks.rate_limit.enabled": "true",
            "checks.rate_limit.request_burst": "20",
            "checks.rate_limit.burst_window_seconds": "5",
            "checks.input_handling.enabled": "true",
            "checks.input_handling.max_payload_kb": "10240",
        }

        response = client.post("/config/save", data=form_data)
        assert response.status_code == 200

        # The saved file is loadable by the existing pydantic config loader
        from sentinel.config import load_config
        reloaded = load_config(good_config)
        assert reloaded.meta.project == "test-project"
        assert reloaded.meta.timeout_seconds == 8
        assert reloaded.endpoints[0].path == "/resource/{id}"
        assert reloaded.endpoints[0].test_ids == [1, 2]
