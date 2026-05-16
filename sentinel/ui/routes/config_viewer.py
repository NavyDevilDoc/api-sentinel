"""Read-only config viewer routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from sentinel.ui.server import templates
from sentinel.ui.services.config_io import (
    env_var_status,
    load_config_for_viewer,
    resolve_config_path,
)

router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
async def view_config(
    request: Request,
    path: str | None = Query(
        None,
        description="Optional path to a sentinel_config.yaml. "
                    "Defaults to ./sentinel_config.yaml.",
    ),
) -> HTMLResponse:
    try:
        resolved = resolve_config_path(path)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={
                "title": "Config Viewer",
                "result": None,
                "error": str(e),
                "requested_path": path,
                "resolved_path": None,
                "token_primary": None,
                "token_secondary": None,
            },
            status_code=400,
        )

    result = load_config_for_viewer(resolved)

    context: dict = {
        "title": "Config Viewer",
        "result": result,
        "error": None,
        "requested_path": path,
        "resolved_path": str(resolved),
        "token_primary": None,
        "token_secondary": None,
    }

    if result.loaded:
        config = result.config
        context["token_primary"] = env_var_status(config.auth.token_primary)
        context["token_secondary"] = env_var_status(config.auth.token_secondary)

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context=context,
    )
