"""Environment variable picker endpoint.

Returns an HTML fragment of `<option>` elements for env var **names**
matching a prefix. The Phase UI-5 config editor will consume this via
`<select hx-get="/env-vars" hx-trigger="load">` to populate token fields.

Critical invariant: this endpoint returns names only. Values are never
read, never serialized, never transmitted.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from sentinel.ui.server import templates
from sentinel.ui.services.env_vars import DEFAULT_PREFIX, list_env_var_names

router = APIRouter()


@router.get("/env-vars", response_class=HTMLResponse)
async def env_var_options(
    request: Request,
    prefix: str = Query(
        DEFAULT_PREFIX,
        description="Env var name prefix to filter by (default: SENTINEL_).",
    ),
    selected: str | None = Query(
        None,
        description="If set and present in the result list, mark this name selected.",
    ),
) -> HTMLResponse:
    names = list_env_var_names(prefix)
    effective_prefix = prefix or DEFAULT_PREFIX
    return templates.TemplateResponse(
        request=request,
        name="partials/env_var_options.html",
        context={
            "names": names,
            "prefix": effective_prefix,
            "selected": selected,
        },
    )
