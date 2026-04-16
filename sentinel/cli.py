"""CLI entry point for API Sentinel.

Usage: sentinel [--config PATH] [--output FORMAT] [--severity LEVEL]
               [--checks CATEGORIES] [--fail-on LEVEL] [--report llm]
               [--llm-backend BACKEND]
"""

from __future__ import annotations

import asyncio
import argparse
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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all CLI flags."""
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="API Sentinel — Automated API Security Checker",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("sentinel_config.yaml"),
        help="Path to sentinel_config.yaml (default: ./sentinel_config.yaml)",
    )
    parser.add_argument(
        "--output",
        choices=["terminal", "json", "both"],
        default="terminal",
        help="Output format (default: terminal)",
    )
    parser.add_argument(
        "--severity",
        choices=["critical", "warning", "info", "all"],
        default="all",
        help="Minimum severity level to display (default: all)",
    )
    parser.add_argument(
        "--checks",
        nargs="+",
        choices=list(CHECK_NAME_MAP.keys()),
        default=["all"],
        help="Run only specific check categories (default: all)",
    )
    parser.add_argument(
        "--fail-on",
        choices=["critical", "warning", "any"],
        default="critical",
        help="Exit code 1 if findings exist at this severity (default: critical)",
    )
    parser.add_argument(
        "--report",
        choices=["llm"],
        default=None,
        help="Append an LLM-generated narrative report",
    )
    parser.add_argument(
        "--llm-backend",
        choices=["gemini", "claude", "openai", "ollama"],
        default="gemini",
        help="LLM provider for --report llm (default: gemini)",
    )
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


def main() -> int:
    """Main entry point. Returns an exit code integer."""
    console = Console()
    parser = build_parser()
    args = parser.parse_args()

    # Load .env file if present
    load_dotenv_file()

    # Load and validate config
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

    # Resolve CLI check names to internal category names
    selected_checks = _resolve_checks(args.checks)

    # Run checks
    try:
        run_result = asyncio.run(run_checks(config, selected_checks))
    except EnvVarError as e:
        console.print(f"[bold red]Environment error:[/bold red] {e}")
        return EXIT_CONFIG_ERROR
    except Exception as e:
        console.print(f"[bold red]Network/runtime error:[/bold red] {e}")
        return EXIT_NETWORK_ERROR

    # Output
    min_severity = _SEVERITY_MAP[args.severity]

    if args.output in ("terminal", "both"):
        render_terminal_report(run_result, config.meta.base_url, min_severity, console)

    if args.output in ("json", "both"):
        json_path = Path("sentinel_report.json")
        render_json_report(
            run_result, json_path, config, _collect_redact_values(config)
        )
        console.print(f"[dim]Report written to {json_path}[/dim]")

    # LLM narrative report (optional, non-fatal)
    if args.report == "llm":
        from rich.panel import Panel

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
            console.print()
            console.print(Panel(
                narrative,
                title="[bold]LLM Security Analysis[/bold]",
                subtitle=f"[dim]{args.llm_backend}[/dim]",
                style="cyan",
                expand=True,
            ))
        except LLMBackendError as e:
            console.print(f"\n[bold yellow]LLM report skipped:[/bold yellow] {e}")
        except Exception as e:
            console.print(f"\n[bold yellow]LLM report failed:[/bold yellow] {e}")

    # Determine exit code
    if _has_findings_at_threshold(run_result.results, args.fail_on):
        return EXIT_FINDINGS

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
