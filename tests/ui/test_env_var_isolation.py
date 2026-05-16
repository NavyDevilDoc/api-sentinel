"""CRITICAL invariant — token values must NEVER appear in any UI response.

This test sets real environment variables with distinctive canary values,
exercises every UI route currently exposed by the app, and asserts the
canary strings never appear in response bodies or headers.

If this test ever fails, the UI is leaking secrets and the failure is
release-blocking. As new routes are added in later phases, append them to
the `_ROUTES` list — the invariant must hold for every one.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sentinel.ui.server import create_app  # noqa: E402


# Distinctive canary strings — chosen so a grep over a failing response will
# match exactly one source (these tests) and nothing else.
_PRIMARY_SECRET = "ZZZ-primary-leak-canary-9f2a-ZZZ"
_SECONDARY_SECRET = "ZZZ-secondary-leak-canary-7b3c-ZZZ"


_GOOD_YAML = (
    "meta:\n"
    "  project: leak-test\n"
    "  base_url: https://api.example.com\n"
    "auth:\n"
    "  token_primary: SENTINEL_TOKEN_PRIMARY\n"
    "  token_secondary: SENTINEL_TOKEN_SECONDARY\n"
    "endpoints:\n"
    "  - path: /resource/{id}\n"
    "    method: GET\n"
    "    requires_auth: true\n"
    "    test_ids: [1]\n"
    "    owned_by: token_primary\n"
    "checks:\n"
    "  authorization:\n"
    "    enabled: true\n"
)


@pytest.fixture
def configured_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Set canary env vars + write a config that references them."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SENTINEL_TOKEN_PRIMARY", _PRIMARY_SECRET)
    monkeypatch.setenv("SENTINEL_TOKEN_SECONDARY", _SECONDARY_SECRET)

    config = tmp_path / "sentinel_config.yaml"
    config.write_text(_GOOD_YAML, encoding="utf-8")
    return config


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# Every UI route exposed by the app as of Phase UI-7. Append new routes here
# whenever a new phase lands.
#
# For routes with path params (like /scans/{id}), we hit them with a known
# non-existent UUID. The 404 page still needs to be canary-free.
_ROUTES = [
    "/",
    "/healthz",
    "/config",
    "/config/edit",
    "/env-vars",
    "/scans",
    "/scans/00000000-0000-0000-0000-000000000000",
    "/scans/00000000-0000-0000-0000-000000000000/status",
    "/scans/00000000-0000-0000-0000-000000000000/results",
    "/scans/00000000-0000-0000-0000-000000000000/export.json",
    "/static/styles.css",
    "/static/htmx.min.js",
]


def _assert_no_secret_anywhere(response: httpx.Response, label: str) -> None:
    body = response.text
    headers_str = " ".join(f"{k}: {v}" for k, v in response.headers.items())

    for secret in (_PRIMARY_SECRET, _SECONDARY_SECRET):
        assert secret not in body, (
            f"Token value leaked in response BODY of {label}. "
            "This is a release-blocking security bug."
        )
        assert secret not in headers_str, (
            f"Token value leaked in response HEADERS of {label}. "
            "This is a release-blocking security bug."
        )


@pytest.mark.parametrize("route", _ROUTES)
def test_no_token_value_leaks_on_route(
    route: str,
    client: TestClient,
    configured_environment: Path,
) -> None:
    response = client.get(route)
    _assert_no_secret_anywhere(response, route)


def test_no_token_value_leaks_on_config_path_override(
    client: TestClient,
    configured_environment: Path,
) -> None:
    response = client.get(f"/config?path={configured_environment}")
    _assert_no_secret_anywhere(response, "/config?path=...")


def test_token_names_DO_appear_on_config_page(
    client: TestClient, configured_environment: Path
) -> None:
    """Sanity check: the env var NAMES must still render, otherwise the
    isolation test above could pass trivially by hiding everything."""
    response = client.get("/config")
    assert response.status_code == 200
    assert "SENTINEL_TOKEN_PRIMARY" in response.text
    assert "SENTINEL_TOKEN_SECONDARY" in response.text
