"""Scan execution routes.

  GET  /scans                  list page (recent runs + new-scan form)
  POST /scans                  start a scan (303-redirects to detail)
  GET  /scans/{id}             detail page (target, status, results)
  GET  /scans/{id}/status      polling fragment (HTMX every 1s)

Scans take the same `?path=` argument as the editor/viewer. The route
reads the config fresh from disk at scan-start time — no state coupling
to the editor session.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sentinel.checks.base import CheckResult, Severity
from sentinel.config import SentinelConfig
from sentinel.reporter import build_report_data
from sentinel.ui.server import templates
from sentinel.ui.services.config_io import (
    load_config_for_viewer,
    resolve_config_path,
)
from sentinel.ui.services.scan_runner import ScanState, scan_runner

router = APIRouter()


def _collect_redact_values(config: SentinelConfig) -> list[str] | None:
    """Mirror of cli._collect_redact_values — best-effort token redaction
    for JSON export. Returns None if no tokens resolve."""
    values: list[str] = []
    for var_name in [config.auth.token_primary, config.auth.token_secondary]:
        if var_name:
            v = os.environ.get(var_name)
            if v:
                values.append(v)
    return values or None


_SEVERITY_FILTERS = {"all", "critical", "warning", "pass"}


def _apply_severity_filter(
    results: list[CheckResult], filter_name: str
) -> list[CheckResult]:
    """Filter findings for the results panel.

    `critical` shows only critical findings (the "fix this now" view).
    `warning` shows critical + warning (the actionable view).
    `pass` shows only PASS results (sanity check).
    `all` or anything unknown returns everything.
    """
    if filter_name == "critical":
        return [r for r in results if r.severity == Severity.CRITICAL]
    if filter_name == "warning":
        return [
            r
            for r in results
            if r.severity in (Severity.CRITICAL, Severity.WARNING)
        ]
    if filter_name == "pass":
        return [r for r in results if r.severity == Severity.PASS]
    return results


def _group_results_by_category(
    results: list[CheckResult],
) -> dict[str, list[CheckResult]]:
    """Group CheckResults by the prefix of their check_id."""
    groups: dict[str, list[CheckResult]] = {}
    for r in results:
        category = r.check_id.split(".")[0]
        groups.setdefault(category, []).append(r)
    return groups


def _severity_counts(results: list[CheckResult]) -> dict[str, int]:
    return {
        "critical": sum(1 for r in results if r.severity == Severity.CRITICAL),
        "warning": sum(1 for r in results if r.severity == Severity.WARNING),
        "info": sum(1 for r in results if r.severity == Severity.INFO),
        "passed": sum(1 for r in results if r.passed),
        "total": len(results),
    }


@router.get("/scans", response_class=HTMLResponse)
async def list_scans(
    request: Request,
    path: str | None = Query(None),
) -> HTMLResponse:
    """Landing page: recent scan history + new-scan form."""
    recent = await scan_runner.list_recent()
    return templates.TemplateResponse(
        request=request,
        name="scans.html",
        context={
            "title": "Scans",
            "scans": recent,
            "default_path": path or "sentinel_config.yaml",
        },
    )


@router.post("/scans", response_model=None)
async def start_scan(
    request: Request,
    path: str | None = Query(None),
):
    """Load config from disk, kick off a scan, redirect to detail page.

    Returns either an HTMLResponse (error case, re-renders /scans) or a
    303 RedirectResponse to /scans/{id} on success. The dynamic return
    type means no response-model annotation — FastAPI just forwards the
    Response subclass the handler chose.
    """
    try:
        resolved = resolve_config_path(path)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="scans.html",
            context={
                "title": "Scans",
                "scans": await scan_runner.list_recent(),
                "default_path": path or "sentinel_config.yaml",
                "error": str(e),
            },
            status_code=400,
        )

    result = load_config_for_viewer(resolved)
    if not result.loaded:
        return templates.TemplateResponse(
            request=request,
            name="scans.html",
            context={
                "title": "Scans",
                "scans": await scan_runner.list_recent(),
                "default_path": path or str(resolved),
                "error": f"Cannot scan: {result.error}",
            },
            status_code=400,
        )

    scan_id = await scan_runner.start(result.config, str(resolved))

    # Both HTMX and plain form posts handle 303 redirects gracefully.
    return RedirectResponse(url=f"/scans/{scan_id}", status_code=303)


def _render_status_fragment(
    request: Request, scan: ScanState
) -> HTMLResponse:
    """Render the polling fragment with current state."""
    context: dict = {"scan": scan, "active_filter": "all"}
    if scan.run_result is not None:
        context["categories"] = _group_results_by_category(scan.run_result.results)
        context["counts"] = _severity_counts(scan.run_result.results)
    return templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context=context,
    )


@router.get("/scans/{scan_id}", response_class=HTMLResponse)
async def view_scan(scan_id: str, request: Request) -> HTMLResponse:
    """Scan detail page. Renders status + results once available."""
    scan = await scan_runner.get(scan_id)
    if scan is None:
        return templates.TemplateResponse(
            request=request,
            name="scan.html",
            context={
                "title": "Scan not found",
                "scan": None,
                "scan_id": scan_id,
            },
            status_code=404,
        )

    context: dict = {
        "title": f"Scan {scan_id[:8]}",
        "scan": scan,
        "active_filter": "all",
    }
    if scan.run_result is not None:
        context["categories"] = _group_results_by_category(scan.run_result.results)
        context["counts"] = _severity_counts(scan.run_result.results)

    return templates.TemplateResponse(
        request=request,
        name="scan.html",
        context=context,
    )


@router.get("/scans/{scan_id}/results", response_class=HTMLResponse)
async def scan_results_filtered(
    scan_id: str,
    request: Request,
    filter: str = Query("all"),
) -> HTMLResponse:
    """Render just the results panel with a severity filter applied.

    Returns the same `partials/scan_results.html` the detail page uses;
    HTMX swaps it into `#scan-results-area` (outerHTML), preserving the
    surrounding scan metadata and the (now-stopped) polling wrapper.
    """
    scan = await scan_runner.get(scan_id)
    if scan is None or scan.run_result is None:
        return HTMLResponse(
            content='<div class="alert alert-error">Results not available.</div>',
            status_code=404,
        )

    if filter not in _SEVERITY_FILTERS:
        filter = "all"

    all_results = scan.run_result.results
    filtered = _apply_severity_filter(all_results, filter)

    return templates.TemplateResponse(
        request=request,
        name="partials/scan_results.html",
        context={
            "scan": scan,
            # Group filtered results — empty categories drop out naturally
            "categories": _group_results_by_category(filtered),
            # Counts always reflect the FULL result set, not the filtered view
            "counts": _severity_counts(all_results),
            "active_filter": filter,
        },
    )


@router.get("/scans/{scan_id}/export.json")
async def export_scan_json(scan_id: str) -> JSONResponse:
    """Download the scan results as JSON.

    Reuses `build_report_data()` so the output matches what the CLI's
    `--output json` produces. Token values resolved at scan time are
    redacted from string fields.
    """
    scan = await scan_runner.get(scan_id)
    if scan is None or scan.run_result is None:
        return JSONResponse(
            content={"error": "Scan not found or not complete."},
            status_code=404,
        )

    data = build_report_data(
        scan.run_result,
        scan.config,
        redact_values=_collect_redact_values(scan.config),
    )
    filename = f"sentinel-scan-{scan.id[:8]}.json"
    return JSONResponse(
        content=data,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/scans/{scan_id}/status", response_class=HTMLResponse)
async def scan_status(scan_id: str, request: Request) -> HTMLResponse:
    """HTMX polling target. Returns just the status fragment."""
    scan = await scan_runner.get(scan_id)
    if scan is None:
        return HTMLResponse(
            content='<div class="alert alert-error">Scan not found.</div>',
            status_code=404,
        )
    return _render_status_fragment(request, scan)
