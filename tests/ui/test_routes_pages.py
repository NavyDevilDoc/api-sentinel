"""Phase UI-2 — tests for full-page UI routes and static asset serving."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sentinel.ui.server import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestHomeRoute:
    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200

    def test_returns_html_content_type(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.headers["content-type"].startswith("text/html")

    def test_includes_api_sentinel_branding(self, client: TestClient) -> None:
        response = client.get("/")
        assert "API Sentinel" in response.text

    def test_links_to_static_assets(self, client: TestClient) -> None:
        """base.html must reference the htmx and stylesheet assets."""
        response = client.get("/")
        assert "/static/htmx.min.js" in response.text
        assert "/static/styles.css" in response.text


class TestHealthz:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body


class TestStaticAssets:
    def test_styles_css_is_served(self, client: TestClient) -> None:
        response = client.get("/static/styles.css")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")

    def test_htmx_js_is_served(self, client: TestClient) -> None:
        response = client.get("/static/htmx.min.js")
        assert response.status_code == 200
        # Browsers and StaticFiles disagree on the canonical JS MIME type
        assert response.headers["content-type"].startswith(
            ("application/javascript", "text/javascript")
        )

    def test_unknown_static_path_404s(self, client: TestClient) -> None:
        response = client.get("/static/nonexistent.txt")
        assert response.status_code == 404


class TestOpenAPISurfaceHidden:
    """The UI is a frontend, not a public API. Docs surfaces must be off."""

    def test_no_swagger_docs(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 404

    def test_no_redoc(self, client: TestClient) -> None:
        assert client.get("/redoc").status_code == 404

    def test_no_openapi_json(self, client: TestClient) -> None:
        assert client.get("/openapi.json").status_code == 404
