"""Check orchestrator.

Accepts a SentinelConfig, determines which check categories are enabled,
instantiates check classes, calls their run() methods, and collects all
CheckResult objects into a RunResult.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from pydantic import BaseModel

from sentinel.checks.auth import AuthCheck
from sentinel.checks.authorization import AuthorizationCheck
from sentinel.checks.base import BaseCheck, CheckResult, Severity
from sentinel.checks.headers import HeadersCheck
from sentinel.checks.input_handling import InputHandlingCheck
from sentinel.checks.rate_limit import RateLimitCheck
from sentinel.checks.transport import TransportCheck
from sentinel.config import SentinelConfig
from sentinel.utils.http_client import create_client

# All valid check category names
ALL_CHECK_CATEGORIES = [
    "transport",
    "headers",
    "auth",
    "authorization",
    "rate_limit",
    "input_handling",
]

# Registry of implemented check classes. Future phases add entries here.
CHECK_REGISTRY: dict[str, type[BaseCheck]] = {
    "transport": TransportCheck,
    "headers": HeadersCheck,
    "auth": AuthCheck,
    "authorization": AuthorizationCheck,
    "rate_limit": RateLimitCheck,
    "input_handling": InputHandlingCheck,
}


class RunResult(BaseModel):
    """Aggregate result of a full sentinel run."""

    results: list[CheckResult]
    timestamp: datetime
    duration_ms: float
    checks_run: list[str]
    checks_skipped: list[str]


def _resolve_selected_checks(
    config: SentinelConfig,
    selected: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Determine which check categories to run and which to skip.

    Returns:
        (checks_to_run, checks_skipped) tuple.
    """
    if selected is None or "all" in selected:
        candidates = ALL_CHECK_CATEGORIES
    else:
        candidates = selected

    checks_run = []
    checks_skipped = []

    for category in ALL_CHECK_CATEGORIES:
        if category not in candidates:
            checks_skipped.append(category)
            continue

        # Check if the category is enabled in config
        check_config = getattr(config.checks, category, None)
        if check_config is not None and not check_config.enabled:
            checks_skipped.append(category)
            continue

        checks_run.append(category)

    return checks_run, checks_skipped


async def run_checks(
    config: SentinelConfig,
    selected_checks: list[str] | None = None,
) -> RunResult:
    """Run all enabled security checks against the target API.

    Args:
        config: Validated sentinel configuration.
        selected_checks: List of check category names to run, or None for all.

    Returns:
        A RunResult containing all findings.
    """
    start = time.monotonic()

    checks_run, checks_skipped = _resolve_selected_checks(config, selected_checks)

    results: list[CheckResult] = []

    async with create_client(
        base_url=config.meta.base_url,
        timeout_seconds=config.meta.timeout_seconds,
    ) as client:
        for category in checks_run:
            check_cls = CHECK_REGISTRY.get(category)
            if check_cls is None:
                continue  # Category not yet implemented
            check = check_cls()
            # Per-category exception isolation: one check raising should
            # not abort the entire scan. We catch broad Exception (not
            # BaseException, so KeyboardInterrupt etc. still propagate)
            # and surface the failure as a CRITICAL finding scoped to
            # the category. Other categories continue running.
            try:
                category_results = await check.run(config, client)
                results.extend(category_results)
            except Exception as e:
                results.append(CheckResult(
                    check_id=f"{category}.unhandled_exception",
                    name=f"{category} check failed to complete",
                    severity=Severity.CRITICAL,
                    passed=False,
                    detail=f"{type(e).__name__}: {e}",
                    expected="Check should run to completion",
                    recommendation=(
                        "Often indicates a protocol/environment mismatch "
                        "(HTTP/2 negotiation, edge size limits, etc.). "
                        "Try disabling this check category in the config "
                        "or adjusting its settings (e.g. lower "
                        "max_payload_kb for input_handling)."
                    ),
                ))

    elapsed_ms = (time.monotonic() - start) * 1000

    return RunResult(
        results=results,
        timestamp=datetime.now(timezone.utc),
        duration_ms=round(elapsed_ms, 2),
        checks_run=checks_run,
        checks_skipped=checks_skipped,
    )
