"""CLI entry point for API Sentinel.

Usage:
    sentinel scan [--config PATH] [--output FORMAT] ...   run security checks
    sentinel ui   [--host HOST] [--port PORT] ...         start the local web UI
    sentinel init [--spec PATH_OR_URL]                    init a config from OpenAPI

Bare `sentinel ...` (no subcommand) is equivalent to `sentinel scan ...` for
backward compatibility with v0.1.0 invocations like `sentinel --config foo.yaml`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from sentinel.checks.base import Severity
from sentinel.config import load_config
from sentinel.reporter import render_terminal_report, render_json_report
from sentinel.runner import run_checks
from sentinel.utils.env_loader import EnvVarError, load_dotenv_file

# Map CLI shorthand names to internal check category names
CHECK_NAME_MAP = {
    "transport": "transport",
    "headers": "headers",
    "auth": "auth",
    "authorization": "authorization",
    "rate_limit": "rate_limit",
    "input": "input_handling",
    "all": "all",
}

# Map --severity and --fail-on values to Severity enum
_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
    "all": Severity.PASS,
}

# Exit codes
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_CONFIG_ERROR = 2
EXIT_NETWORK_ERROR = 3

# Subcommands recognized at the top level
_KNOWN_SUBCOMMANDS: frozenset[str] = frozenset({"scan", "ui", "init"})


def _normalize_argv(argv: list[str]) -> list[str]:
    """Inject `scan` as the default subcommand for backward compatibility.

    `sentinel --config X.yaml` is rewritten to `sentinel scan --config X.yaml`
    so v0.1.0 invocations and CI scripts keep working unchanged. Explicit
    subcommands and top-level help requests are left alone.
    """
    if not argv:
        return ["scan"]
    first = argv[0]
    if first in _KNOWN_SUBCOMMANDS or first in ("-h", "--help"):
        return argv
    return ["scan", *argv]


def _add_scan_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        type=Path,
        default=Path("sentinel_config.yaml"),
        help="Path to sentinel_config.yaml (default: ./sentinel_config.yaml)",
    )
    p.add_argument(
        "--output",
        choices=["terminal", "json", "both"],
        default="terminal",
        help="Output format (default: terminal)",
    )
    p.add_argument(
        "--severity",
        choices=["critical", "warning", "info", "all"],
        default="all",
        help="Minimum severity level to display (default: all)",
    )
    p.add_argument(
        "--checks",
        nargs="+",
        choices=list(CHECK_NAME_MAP.keys()),
        default=["all"],
        help="Run only specific check categories (default: all)",
    )
    p.add_argument(
        "--fail-on",
        choices=["critical", "warning", "any"],
        default="critical",
        help="Exit code 1 if findings exist at this severity (default: critical)",
    )
    p.add_argument(
        "--report",
        choices=["llm"],
        default=None,
        help="Append an LLM-generated narrative report",
    )
    p.add_argument(
        "--llm-backend",
        choices=["gemini", "claude", "openai", "ollama"],
        default="gemini",
        help="LLM provider for --report llm (default: gemini)",
    )


def _add_ui_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind to (default: 127.0.0.1, localhost-only)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on (default: 8765; falls back to a free port if in use)",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the browser after starting the server",
    )


def _add_init_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--spec",
        default=None,
        help="Path or URL to an OpenAPI/Swagger spec to import",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="API Sentinel — Automated API Security Checker",
    )
    sub = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="{scan,ui,init}",
    )

    scan_p = sub.add_parser(
        "scan",
        help="Run security checks against an API (default subcommand)",
    )
    _add_scan_flags(scan_p)

    ui_p = sub.add_parser(
        "ui",
        help="Start the local web UI (requires the [ui] extra)",
    )
    _add_ui_flags(ui_p)

    init_p = sub.add_parser(
        "init",
        help="Initialize a sentinel_config.yaml from an OpenAPI spec",
    )
    _add_init_flags(init_p)

    return parser


def _resolve_checks(raw_checks: list[str]) -> list[str] | None:
    """Map CLI check names to internal category names.

    Returns None if 'all' is selected (meaning run everything).
    """
    if "all" in raw_checks:
        return None
    return [CHECK_NAME_MAP[name] for name in raw_checks]


def _has_findings_at_threshold(
    results: list,
    fail_on: str,
) -> bool:
    """Determine if any results meet the --fail-on threshold."""
    if fail_on == "any":
        return any(not r.passed for r in results)
    elif fail_on == "critical":
        return any(r.severity == Severity.CRITICAL for r in results)
    elif fail_on == "warning":
        return any(
            r.severity in (Severity.CRITICAL, Severity.WARNING) for r in results
        )
    return False


def _collect_redact_values(config) -> list[str] | None:
    """Collect resolved token values for redaction (best-effort, non-fatal)."""
    import os

    values: list[str] = []
    for var_name in [config.auth.token_primary, config.auth.token_secondary]:
        if var_name:
            val = os.environ.get(var_name)
            if val:
                values.append(val)
    return values or None


def _render_llm_narrative(
    console: Console, narrative: str, backend: str
) -> None:
    """Print the LLM narrative inside a Rich panel.

    Escapes `[bracket]` sequences before rendering — LLM output is
    free-form text and may legitimately contain things like
    "see [section 3]" or "the [403] response code" that Rich would
    otherwise parse as unknown markup tags and silently drop.
    """
    from rich.markup import escape
    from rich.panel import Panel

    console.print()
    console.print(Panel(
        escape(narrative),
        title="[bold]LLM Security Analysis[/bold]",
        subtitle=f"[dim]{backend}[/dim]",
        style="cyan",
        expand=True,
    ))


def _run_scan(args: argparse.Namespace, console: Console) -> int:
    """Execute the scan subcommand. Returns an exit code."""
    load_dotenv_file()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        return EXIT_CONFIG_ERROR
    except ValidationError as e:
        console.print(f"[bold red]Config validation error:[/bold red]\n{e}")
        return EXIT_CONFIG_ERROR
    except ValueError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        return EXIT_CONFIG_ERROR

    selected_checks = _resolve_checks(args.checks)

    try:
        run_result = asyncio.run(run_checks(config, selected_checks))
    except EnvVarError as e:
        console.print(f"[bold red]Environment error:[/bold red] {e}")
        return EXIT_CONFIG_ERROR
    except Exception as e:
        console.print(f"[bold red]Network/runtime error:[/bold red] {e}")
        return EXIT_NETWORK_ERROR

    min_severity = _SEVERITY_MAP[args.severity]

    if args.output in ("terminal", "both"):
        render_terminal_report(run_result, config.meta.base_url, min_severity, console)

    if args.output in ("json", "both"):
        json_path = Path("sentinel_report.json")
        render_json_report(
            run_result, json_path, config, _collect_redact_values(config)
        )
        console.print(f"[dim]Report written to {json_path}[/dim]")

    if args.report == "llm":
        from sentinel.llm import LLMBackendError, generate_llm_report

        try:
            narrative = asyncio.run(
                generate_llm_report(
                    run_result,
                    config,
                    backend=args.llm_backend,
                    redact_values=_collect_redact_values(config),
                )
            )
            _render_llm_narrative(console, narrative, args.llm_backend)
        except LLMBackendError as e:
            console.print(f"\n[bold yellow]LLM report skipped:[/bold yellow] {e}")
        except Exception as e:
            console.print(f"\n[bold yellow]LLM report failed:[/bold yellow] {e}")

    if _has_findings_at_threshold(run_result.results, args.fail_on):
        return EXIT_FINDINGS

    return EXIT_OK


def _run_ui(args: argparse.Namespace, console: Console) -> int:
    """Start the local web UI. Requires the [ui] extra to be installed."""
    # Load .env so users who keep tokens in .env (matching the scan
    # command's behavior) see them in the UI's env-var picker. Loaded
    # once at server startup — editing .env afterward needs a UI restart.
    load_dotenv_file()

    try:
        from sentinel.ui.launcher import launch
    except ImportError as e:
        # The import gate in sentinel/ui/__init__.py already includes the
        # `pip install 'api-sentinel[ui]'` hint; surface it via Rich.
        # \\[ui] escapes the [ so Rich doesn't parse it as a markup tag.
        console.print(
            f"[bold red]Cannot start UI:[/bold red] {e}\n"
            "Install the UI extras: pip install 'api-sentinel\\[ui]'"
        )
        return EXIT_CONFIG_ERROR

    return launch(
        host=args.host,
        port=args.port,
        no_browser=args.no_browser,
        console=console,
    )


def _run_init_stub(args: argparse.Namespace, console: Console) -> int:
    """Stub for `sentinel init`. Real implementation is a separate roadmap item."""
    console.print(
        "[bold yellow]`sentinel init` is not yet implemented.[/bold yellow]\n"
        "OpenAPI auto-config is on the roadmap "
        "(FUTURE_DEVELOPMENTS.md, Category 2)."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns an exit code integer.

    Args:
        argv: Optional argument list. Defaults to sys.argv[1:]. Accepting an
            explicit argv makes the dispatch testable without monkeypatching
            sys.argv.
    """
    console = Console()
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    normalized = _normalize_argv(raw_argv)

    parser = build_parser()
    args = parser.parse_args(normalized)

    if args.command == "scan" or args.command is None:
        return _run_scan(args, console)
    if args.command == "ui":
        return _run_ui(args, console)
    if args.command == "init":
        return _run_init_stub(args, console)

    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
