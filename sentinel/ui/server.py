"""FastAPI application factory for the API Sentinel UI.

The factory pattern (`create_app()`) lets tests instantiate fresh apps without
import-time side effects, and leaves room for future config injection
(custom static paths, debug flags) without changing the public API.

Templates are module-level for ergonomic access from route modules — Jinja
template renderers are stateless and safe to share.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sentinel

_UI_DIR = Path(__file__).parent
_TEMPLATES_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _as_lines(value: object) -> str:
    """Render a list-or-string as a newline-joined string for textareas.

    Form data arrives as strings; model_dump() produces lists. The editor
    re-renders both shapes through the same template, so this filter
    unifies the two without inline `is iterable` checks everywhere.
    """
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    if isinstance(value, str):
        return value
    return ""


templates.env.filters["as_lines"] = _as_lines


def create_app() -> FastAPI:
    """Build and return a configured FastAPI app."""
    app = FastAPI(
        title="API Sentinel UI",
        description="Localhost-only web frontend for API Sentinel.",
        # No public docs/redoc — this is a UI surface, not a public API
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # Import route modules lazily inside the factory so loading server.py
    # doesn't pull in everything transitively at module-import time.
    from sentinel.ui.routes import (
        config_editor,
        config_viewer,
        env,
        pages,
        scans,
    )

    app.include_router(pages.router)
    app.include_router(config_viewer.router)
    app.include_router(config_editor.router)
    app.include_router(env.router)
    app.include_router(scans.router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Cheap readiness probe — used by the launcher and tests."""
        return {"status": "ok", "version": sentinel.__version__}

    return app
