"""Security header checks.

Validates required security headers are present, forbidden information-leaking
headers are absent, and CORS is not misconfigured with a wildcard origin.
All checks are performed against a single GET to the base URL.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

from sentinel.checks.base import BaseCheck, CheckResult, Severity, make_result

if TYPE_CHECKING:
    from sentinel.config import SentinelConfig


def _normalize_header_name(name: str) -> str:
    """Normalize a header name for use in check_id: lowercase, hyphens to underscores."""
    return name.lower().replace("-", "_")


class HeadersCheck(BaseCheck):
    """Security header checks."""

    check_category = "headers"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        start = time.monotonic()
        try:
            response = await client.get("/")
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            return [CheckResult(
                check_id="headers.connection_error",
                name="Header check connection",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Could not connect to check headers: {e}",
                expected="Successful HTTP response",
                recommendation="Verify the server is reachable.",
            )]
        latency = round((time.monotonic() - start) * 1000, 2)

        results: list[CheckResult] = []
        results.extend(
            self._check_required_headers(config, response, latency)
        )
        results.extend(
            self._check_forbidden_headers(config, response, latency)
        )

        cors_result = self._check_cors_wildcard(response, latency)
        if cors_result is not None:
            results.append(cors_result)

        return results

    def _check_required_headers(
        self,
        config: SentinelConfig,
        response: httpx.Response,
        latency_ms: float,
    ) -> list[CheckResult]:
        """Check that each required security header is present."""
        results: list[CheckResult] = []
        for header in config.checks.headers.required:
            normalized = _normalize_header_name(header)
            value = response.headers.get(header)

            if value is not None:
                results.append(make_result(
                    check_id=f"headers.required_{normalized}",
                    name=f"{header} present",
                    passed=True,
                    detail=f"{header}: {value}",
                    expected=f"{header} header present",
                    endpoint="/",
                    response_code=response.status_code,
                    latency_ms=latency_ms,
                ))
            else:
                results.append(make_result(
                    check_id=f"headers.required_{normalized}",
                    name=f"{header} present",
                    passed=False,
                    detail=f"{header} header is missing",
                    expected=f"{header} header present",
                    recommendation=f"Add the {header} header to all API responses.",
                    endpoint="/",
                    response_code=response.status_code,
                    latency_ms=latency_ms,
                ))
        return results

    def _check_forbidden_headers(
        self,
        config: SentinelConfig,
        response: httpx.Response,
        latency_ms: float,
    ) -> list[CheckResult]:
        """Check that forbidden information-leaking headers are absent."""
        results: list[CheckResult] = []
        for header in config.checks.headers.forbidden_leakage:
            normalized = _normalize_header_name(header)
            value = response.headers.get(header)

            if value is None:
                results.append(make_result(
                    check_id=f"headers.leakage_{normalized}",
                    name=f"{header} not present",
                    passed=True,
                    detail=f"{header} header is not exposed",
                    expected=f"{header} header should not be present",
                    endpoint="/",
                    response_code=response.status_code,
                    latency_ms=latency_ms,
                ))
            else:
                results.append(make_result(
                    check_id=f"headers.leakage_{normalized}",
                    name=f"{header} leaks information",
                    passed=False,
                    detail=f"{header} header exposes: {value}",
                    expected=f"{header} header should not be present",
                    recommendation=f"Remove or suppress the {header} header.",
                    endpoint="/",
                    response_code=response.status_code,
                    latency_ms=latency_ms,
                ))
        return results

    def _check_cors_wildcard(
        self,
        response: httpx.Response,
        latency_ms: float,
    ) -> CheckResult | None:
        """Check for dangerous CORS wildcard configuration.

        Returns None if no CORS header is present (nothing to report).
        """
        cors_value = response.headers.get("access-control-allow-origin")
        if cors_value is None:
            return None

        if cors_value == "*":
            return CheckResult(
                check_id="headers.cors_wildcard",
                name="CORS allows wildcard origin",
                severity=Severity.WARNING,
                passed=False,
                detail="Access-Control-Allow-Origin: *",
                expected="CORS should restrict allowed origins",
                recommendation="Replace wildcard (*) with specific allowed origins.",
                endpoint="/",
                response_code=response.status_code,
                latency_ms=latency_ms,
            )

        return CheckResult(
            check_id="headers.cors_wildcard",
            name="CORS origin restricted",
            severity=Severity.PASS,
            passed=True,
            detail=f"Access-Control-Allow-Origin: {cors_value}",
            expected="CORS should restrict allowed origins",
            recommendation="",
            endpoint="/",
            response_code=response.status_code,
            latency_ms=latency_ms,
        )
