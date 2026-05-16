"""Config editor routes — GET form, POST save, POST add/remove endpoint rows.

All four endpoints share one render path. GET returns the full page; the
three POST endpoints return just the form partial so HTMX can swap it
in-place without reloading the rest of the page.

The form is stateless on the server side. Every add/remove/save request
carries the full current form data, the route mutates the in-memory dict
as needed, and the response re-renders the form. There is no per-user
session, no in-progress draft to track.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from sentinel.config import SentinelConfig
from sentinel.ui.server import templates
from sentinel.ui.services.config_io import (
    load_config_for_viewer,
    resolve_config_path,
)
from sentinel.ui.services.config_writer import write_config_yaml
from sentinel.ui.services.env_vars import list_env_var_names
from sentinel.ui.services.form_parser import (
    loc_to_field_name,
    parse_form_to_dict,
    split_csv_ints,
    split_lines,
)

router = APIRouter()


def _empty_form_data() -> dict[str, Any]:
    """Default values for first-run users with no config yet."""
    return {
        "meta": {"project": "", "base_url": "", "timeout_seconds": 10},
        "auth": {"token_primary": "", "token_secondary": ""},
        "endpoints": [],
        "checks": {
            "transport": {
                "enabled": True,
                "require_https_redirect": True,
                "min_tls_version": "1.2",
                "check_cert_expiry_days": 30,
            },
            "headers": {
                "enabled": True,
                "required": [],
                "forbidden_leakage": [],
            },
            "auth": {"enabled": True},
            "authorization": {"enabled": False},
            "rate_limit": {
                "enabled": True,
                "request_burst": 20,
                "burst_window_seconds": 5,
            },
            "input_handling": {"enabled": True, "max_payload_kb": 10240},
        },
    }


def _coerce_for_pydantic(data: dict[str, Any]) -> dict[str, Any]:
    """Transform raw parsed-form data into the shape pydantic expects.

    Form values arrive as strings everywhere; pydantic handles most coercion
    (str→int, "true"/"false"→bool), but a few list-shaped fields come from
    textareas / csv inputs and need explicit splitting first. Empty optional
    fields are converted to None. Required-but-omitted keys (`endpoints`)
    get a sensible empty default — a form with zero endpoints is valid.
    """
    # Pydantic requires `endpoints` (no default). A form with no endpoint
    # rows produces no `endpoints[N]` keys, so the parsed dict is missing
    # the key entirely. Default to empty list before validation.
    if not isinstance(data.get("endpoints"), list):
        data["endpoints"] = []

    # Headers — textareas with one name per line
    headers_cfg = data.get("checks", {}).get("headers")
    if isinstance(headers_cfg, dict):
        if isinstance(headers_cfg.get("required"), str):
            headers_cfg["required"] = split_lines(headers_cfg["required"])
        if isinstance(headers_cfg.get("forbidden_leakage"), str):
            headers_cfg["forbidden_leakage"] = split_lines(
                headers_cfg["forbidden_leakage"]
            )

    # Endpoints — test_ids comma-separated, owned_by empty→None
    for ep in data.get("endpoints", []):
        if not isinstance(ep, dict):
            continue
        if isinstance(ep.get("test_ids"), str):
            ep["test_ids"] = split_csv_ints(ep["test_ids"])
        if ep.get("owned_by") == "":
            ep["owned_by"] = None

    # Optional secondary token
    auth = data.get("auth")
    if isinstance(auth, dict) and auth.get("token_secondary") == "":
        auth["token_secondary"] = None

    return data


def _data_from_validated_config(config: SentinelConfig) -> dict[str, Any]:
    """Pydantic model → form-friendly dict (compatible with template loops)."""
    return config.model_dump()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`. Override wins for scalars."""
    result: dict[str, Any] = {}
    for key in set(base) | set(override):
        if key in base and key in override:
            b, o = base[key], override[key]
            if isinstance(b, dict) and isinstance(o, dict):
                result[key] = _deep_merge(b, o)
            else:
                result[key] = o
        elif key in override:
            result[key] = override[key]
        else:
            result[key] = base[key]
    return result


def _normalize_bool_strings(node: Any) -> Any:
    """Recursively convert the literal strings "true"/"false" to bool.

    Form data arrives as strings. The template's `{% if value %}` is truthy
    for ANY non-empty string — including "false" — which would render
    unchecked checkboxes as checked after a re-render. Converting to real
    bools fixes the truthiness.
    """
    if isinstance(node, dict):
        return {k: _normalize_bool_strings(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize_bool_strings(v) for v in node]
    if node == "true":
        return True
    if node == "false":
        return False
    return node


def _normalize_for_template(data: dict[str, Any]) -> dict[str, Any]:
    """Make `data` safe for template rendering regardless of source.

    Whether `data` came from `model_dump()` (full schema, real types) or
    from a partially-filled form POST (missing keys, string types), the
    template sees the same shape: all expected keys present, booleans as
    real bools, lists/strings preserved as-is for the `as_lines` filter.
    """
    merged = _deep_merge(_empty_form_data(), data)
    return _normalize_bool_strings(merged)


def _resolve_or_error(
    request: Request, raw_path: str | None
) -> tuple[Path | None, HTMLResponse | None]:
    """Resolve a path with traversal protection, returning either a Path or
    a pre-rendered 400 response."""
    try:
        resolved = resolve_config_path(raw_path)
    except ValueError as e:
        return None, templates.TemplateResponse(
            request=request,
            name="config_edit.html",
            context={
                "title": "Edit Configuration",
                "data": _empty_form_data(),
                "errors": {},
                "save_success": False,
                "save_warning": str(e),
                "resolved_path": "",
                "requested_path": raw_path,
                "backup_enabled": False,
            },
            status_code=400,
        )
    return resolved, None


def _render(
    request: Request,
    *,
    template: str,
    resolved_path: Path,
    requested_path: str | None,
    data: dict[str, Any],
    errors: dict[str, str] | None = None,
    save_success: bool = False,
    save_warning: str | None = None,
    backup_enabled: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "title": "Edit Configuration",
            "data": _normalize_for_template(data),
            "errors": errors or {},
            "save_success": save_success,
            "save_warning": save_warning,
            "resolved_path": str(resolved_path),
            "requested_path": requested_path,
            "backup_enabled": backup_enabled,
            # Server-rendered options for token <select>s. Avoids the HTMX
            # target-inheritance gotcha where load-triggered selects inside
            # a form with hx-target=#wrapper would replace the whole form.
            "env_var_names": list_env_var_names(),
        },
        status_code=status_code,
    )


@router.get("/config/edit", response_class=HTMLResponse)
async def edit_config(
    request: Request,
    path: str | None = Query(None),
) -> HTMLResponse:
    """Load the config (or empty defaults) and render the full editor page."""
    resolved, err = _resolve_or_error(request, path)
    if err is not None:
        return err

    result = load_config_for_viewer(resolved)
    if result.loaded:
        data = _data_from_validated_config(result.config)
        warning = None
    else:
        data = _empty_form_data()
        warning = result.error if result.error and "No config file" not in result.error else None

    return _render(
        request,
        template="config_edit.html",
        resolved_path=resolved,
        requested_path=path,
        data=data,
        save_warning=warning,
    )


async def _parse_form_state(request: Request) -> dict[str, Any]:
    """Read POST form, parse to nested dict (without coercion yet)."""
    form = await request.form()
    flat = {k: v for k, v in form.items() if isinstance(v, str)}
    return parse_form_to_dict(flat)


@router.post("/config/endpoints/add", response_class=HTMLResponse)
async def add_endpoint(
    request: Request,
    path: str | None = Query(None),
) -> HTMLResponse:
    resolved, err = _resolve_or_error(request, path)
    if err is not None:
        return err

    data = await _parse_form_state(request)
    data.setdefault("endpoints", [])
    if not isinstance(data["endpoints"], list):
        data["endpoints"] = []
    data["endpoints"].append({
        "path": "",
        "method": "GET",
        "requires_auth": True,
        "test_ids": "",
        "owned_by": "",
        "rate_limit_sensitive": False,
    })

    return _render(
        request,
        template="partials/config_form.html",
        resolved_path=resolved,
        requested_path=path,
        data=data,
    )


@router.post("/config/endpoints/remove", response_class=HTMLResponse)
async def remove_endpoint(
    request: Request,
    index: int = Query(...),
    path: str | None = Query(None),
) -> HTMLResponse:
    resolved, err = _resolve_or_error(request, path)
    if err is not None:
        return err

    data = await _parse_form_state(request)
    endpoints = data.get("endpoints", [])
    if isinstance(endpoints, list) and 0 <= index < len(endpoints):
        endpoints.pop(index)
        data["endpoints"] = endpoints

    return _render(
        request,
        template="partials/config_form.html",
        resolved_path=resolved,
        requested_path=path,
        data=data,
    )


@router.post("/config/save", response_class=HTMLResponse)
async def save_config(
    request: Request,
    path: str | None = Query(None),
    backup: bool = Form(False),
) -> HTMLResponse:
    resolved, err = _resolve_or_error(request, path)
    if err is not None:
        return err

    data = await _parse_form_state(request)
    # Strip backup field — it's metadata about the save, not config content.
    data.pop("backup", None)

    coerced = _coerce_for_pydantic(data)

    try:
        config = SentinelConfig.model_validate(coerced)
    except ValidationError as e:
        errors: dict[str, str] = {}
        for err_dict in e.errors():
            field = loc_to_field_name(err_dict["loc"])
            errors[field] = err_dict["msg"]
        return _render(
            request,
            template="partials/config_form.html",
            resolved_path=resolved,
            requested_path=path,
            data=coerced,
            errors=errors,
            backup_enabled=backup,
            status_code=422,
        )

    write_config_yaml(resolved, config.model_dump(), backup=backup)

    return _render(
        request,
        template="partials/config_form.html",
        resolved_path=resolved,
        requested_path=path,
        data=config.model_dump(),
        save_success=True,
        backup_enabled=backup,
    )
