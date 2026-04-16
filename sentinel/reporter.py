"""Rich terminal report and JSON export.

Consumes RunResult and renders a color-coded terminal report matching
the format specified in CLAUDE.md, or exports structured JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import sentinel
from sentinel.checks.base import CheckResult, Severity
from sentinel.config import SentinelConfig
from sentinel.runner import RunResult

# Severity display order (most severe first)
_SEVERITY_ORDER = [Severity.CRITICAL, Severity.WARNING, Severity.INFO, Severity.PASS]

_SEVERITY_ICON = {
    Severity.CRITICAL: "[bold red]X[/bold red]",
    Severity.WARNING: "[yellow]![/yellow]",
    Severity.INFO: "[blue]i[/blue]",
    Severity.PASS: "[green]*[/green]",
}

_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
    Severity.PASS: 3,
}


def _make_console() -> Console:
    """Create a Console configured for safe output on all platforms."""
    # Force UTF-8 on Windows to avoid cp1252 encoding errors with box-drawing chars
    if sys.platform == "win32":
        import io

        return Console(file=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    return Console()


def _filter_by_severity(
    results: list[CheckResult],
    min_severity: Severity,
) -> list[CheckResult]:
    """Filter results to only include those at or above the minimum severity."""
    if min_severity == Severity.PASS:
        return results
    threshold = _SEVERITY_RANK[min_severity]
    return [r for r in results if _SEVERITY_RANK[r.severity] <= threshold]


def _group_by_category(results: list[CheckResult]) -> dict[str, list[CheckResult]]:
    """Group results by check category (prefix of check_id before first dot)."""
    groups: dict[str, list[CheckResult]] = {}
    for result in results:
        category = result.check_id.split(".")[0]
        groups.setdefault(category, []).append(result)
    return groups


def render_terminal_report(
    run_result: RunResult,
    base_url: str,
    min_severity: Severity = Severity.PASS,
    console: Console | None = None,
) -> None:
    """Render the Rich terminal report.

    Args:
        run_result: The aggregate results from a sentinel run.
        base_url: Target API base URL for the header.
        min_severity: Minimum severity level to display.
        console: Optional console instance (for testing capture).
    """
    if console is None:
        console = _make_console()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Header panel ---
    header = Text.assemble(
        ("API SENTINEL -- SECURITY REPORT\n", "bold white"),
        (f"Target: {base_url}      {now}", "dim"),
    )
    console.print(Panel(header, style="bold cyan", expand=True))

    filtered = _filter_by_severity(run_result.results, min_severity)

    if not filtered:
        if not run_result.results:
            console.print("\n  [dim]No checks executed.[/dim]\n")
        else:
            console.print(
                f"\n  [dim]No findings at severity "
                f"{min_severity.value} or above.[/dim]\n"
            )
        _render_summary(console, run_result.results)
        return

    # --- Per-category results ---
    groups = _group_by_category(filtered)
    for category, results in groups.items():
        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        console.print(
            f"\n  [bold]{category.upper()}[/bold]"
            f"    [{passed_count}/{total} passed]"
        )
        for r in results:
            icon = _SEVERITY_ICON[r.severity]
            console.print(f"  {icon}  {r.name:<45} {r.severity.value.upper()}")

    # --- Summary ---
    _render_summary(console, run_result.results)

    # --- Critical findings detail ---
    criticals = [r for r in run_result.results if r.severity == Severity.CRITICAL]
    if criticals:
        console.print("\n  [bold red]CRITICAL FINDINGS[/bold red]")
        for r in criticals:
            detail_text = (
                f"[bold]{r.check_id}[/bold]\n"
                f"{r.detail}\n"
                f"[green]Fix:[/green] {r.recommendation}"
            )
            console.print(Panel(detail_text, width=60, padding=(0, 2)))


def _render_summary(console: Console, results: list[CheckResult]) -> None:
    """Render the summary line with severity counts."""
    console.print()
    console.print("-" * 60)

    critical_count = sum(1 for r in results if r.severity == Severity.CRITICAL)
    warning_count = sum(1 for r in results if r.severity == Severity.WARNING)
    passed_count = sum(1 for r in results if r.passed)
    total = len(results)

    console.print(
        f"  [bold]SUMMARY[/bold]    "
        f"Critical: [red]{critical_count}[/red]   "
        f"Warnings: [yellow]{warning_count}[/yellow]   "
        f"Passed: [green]{passed_count}/{total}[/green]"
    )
    console.print("-" * 60)


def _redact_tokens(
    results_data: list[dict],
    token_values: list[str],
) -> list[dict]:
    """Replace known token values with [REDACTED] in all string fields.

    Args:
        results_data: List of CheckResult dicts (from model_dump).
        token_values: Exact token strings to scrub.

    Returns:
        The same list with token values replaced.
    """
    if not token_values:
        return results_data

    # Filter out empty strings to avoid replacing everything
    secrets = [v for v in token_values if v]

    if not secrets:
        return results_data

    for result in results_data:
        for key, value in result.items():
            if isinstance(value, str):
                for secret in secrets:
                    if secret in value:
                        value = value.replace(secret, "[REDACTED]")
                result[key] = value

    return results_data


def build_report_data(
    run_result: RunResult,
    config: SentinelConfig,
    redact_values: list[str] | None = None,
) -> dict:
    """Build the structured report dict without writing to disk.

    This is the shared data contract consumed by both JSON export and
    the LLM report feature.

    Args:
        run_result: The aggregate results from a sentinel run.
        config: The sentinel configuration (for project metadata).
        redact_values: Optional list of secret strings to scrub from output.

    Returns:
        The complete report as a dict.
    """
    results_data = [r.model_dump(mode="json") for r in run_result.results]

    # Redact token values
    if redact_values:
        results_data = _redact_tokens(results_data, redact_values)

    # Build per-category grouping
    results_by_category: dict[str, list[dict]] = {}
    for result_dict in results_data:
        category = result_dict["check_id"].split(".")[0]
        results_by_category.setdefault(category, []).append(result_dict)

    # Severity counts
    critical = sum(1 for r in run_result.results if r.severity == Severity.CRITICAL)
    warning = sum(1 for r in run_result.results if r.severity == Severity.WARNING)
    info = sum(1 for r in run_result.results if r.severity == Severity.INFO)
    passed = sum(1 for r in run_result.results if r.passed)
    total = len(run_result.results)

    return {
        "meta": {
            "tool": "api-sentinel",
            "version": sentinel.__version__,
            "project": config.meta.project,
            "base_url": config.meta.base_url,
            "timestamp": run_result.timestamp.isoformat(),
            "duration_ms": run_result.duration_ms,
        },
        "summary": {
            "total": total,
            "passed": passed,
            "critical": critical,
            "warning": warning,
            "info": info,
            "checks_run": run_result.checks_run,
            "checks_skipped": run_result.checks_skipped,
        },
        "results_by_category": results_by_category,
        "results": results_data,
    }


def render_json_report(
    run_result: RunResult,
    output_path: Path,
    config: SentinelConfig,
    redact_values: list[str] | None = None,
) -> None:
    """Export run results as structured JSON.

    Args:
        run_result: The aggregate results from a sentinel run.
        output_path: File path for the JSON output.
        config: The sentinel configuration (for project metadata).
        redact_values: Optional list of secret strings to scrub from output.
    """
    data = build_report_data(run_result, config, redact_values)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
