"""Input handling checks.

Tests oversized payload rejection, malformed content-type handling,
and basic injection probe strings in query parameters. The tool sends
static, read-only probe strings and never executes responses it receives.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from sentinel.checks.base import (
    BaseCheck,
    CheckResult,
    Severity,
    endpoint_slug,
    make_result,
    resolve_path,
)
from sentinel.utils.env_loader import EnvVarError, resolve_env_var

if TYPE_CHECKING:
    from sentinel.config import EndpointConfig, SentinelConfig

# HTTP methods that accept request bodies
_BODY_METHODS = {"POST", "PUT", "PATCH"}

# Static injection probe strings — read-only, never executed
_INJECTION_PROBES = [
    ("sql", "' OR 1=1--"),
    ("xss", "<script>alert(1)</script>"),
    ("ssti", "{{7*7}}"),
    ("path_traversal", "../../../etc/passwd"),
]


def _build_headers(
    endpoint: EndpointConfig, token: str | None
) -> dict[str, str]:
    """Build request headers, including auth if the endpoint requires it."""
    headers: dict[str, str] = {}
    if endpoint.requires_auth and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class InputHandlingCheck(BaseCheck):
    """Input handling and injection probe checks."""

    check_category = "input_handling"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        # Resolve token if any endpoint requires auth
        token: str | None = None
        needs_auth = any(ep.requires_auth for ep in config.endpoints)
        if needs_auth:
            try:
                token = resolve_env_var(
                    config.auth.token_primary,
                    description="primary API token for input handling checks",
                )
            except EnvVarError as e:
                return [CheckResult(
                    check_id="input.token_resolution_error",
                    name="Input handling token resolution",
                    severity=Severity.CRITICAL,
                    passed=False,
                    detail=str(e),
                    expected=f"Environment variable '{config.auth.token_primary}' must be set",
                    recommendation=f"Set the {config.auth.token_primary} environment variable.",
                )]

        max_kb = config.checks.input_handling.max_payload_kb
        results: list[CheckResult] = []

        for ep in config.endpoints:
            slug = endpoint_slug(ep.path)
            resolved = resolve_path(ep)

            if resolved is None:
                results.append(CheckResult(
                    check_id=f"input.path_unresolvable_{slug}",
                    name=f"Path resolution for {ep.path}",
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Path '{ep.path}' has a placeholder but test_ids is empty",
                    expected="test_ids must contain at least one value for parameterized paths",
                    recommendation="Add test_ids to the endpoint configuration.",
                ))
                continue

            headers = _build_headers(ep, token)

            # Body-accepting methods get payload and content-type checks
            if ep.method.upper() in _BODY_METHODS:
                results.append(
                    await self._check_oversized_payload(
                        client, ep, resolved, slug, headers, max_kb
                    )
                )
                results.append(
                    await self._check_malformed_content_type(
                        client, ep, resolved, slug, headers
                    )
                )

            # All methods get injection probe checks
            results.extend(
                await self._check_injection_probes(
                    client, ep, resolved, slug, headers
                )
            )

        return results

    async def _check_oversized_payload(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        headers: dict[str, str],
        max_kb: int,
    ) -> CheckResult:
        """Send a payload exceeding max_payload_kb and verify rejection."""
        check_id = f"input.oversized_payload_{slug}"
        name = f"Oversized payload rejected on {endpoint.path}"

        # Generate payload just over the limit
        payload = b"X" * ((max_kb + 1) * 1024)
        req_headers = {**headers, "Content-Type": "application/octet-stream"}

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers=req_headers,
                content=payload,
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (413, 400):
                return make_result(
                    check_id=check_id, name=name, passed=True,
                    detail=f"Oversized payload rejected with {code}",
                    expected="413 Payload Too Large or 400 Bad Request",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            elif 200 <= code < 300:
                return make_result(
                    check_id=check_id, name=name, passed=False,
                    detail=f"Server accepted oversized payload ({max_kb + 1} KB) with {code}",
                    expected="413 Payload Too Large or 400 Bad Request",
                    recommendation=f"Reject payloads larger than {max_kb} KB.",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            elif code >= 500:
                return CheckResult(
                    check_id=check_id, name=name,
                    severity=Severity.WARNING, passed=False,
                    detail=f"Server returned {code} on oversized payload",
                    expected="413 Payload Too Large or 400 Bad Request",
                    recommendation="Handle oversized payloads gracefully instead of crashing.",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            else:
                # 401, 403, 404, 422, etc. — rejected before payload processing
                return make_result(
                    check_id=check_id, name=name, passed=True,
                    detail=f"Request rejected with {code} before payload processing",
                    expected="413 Payload Too Large or 400 Bad Request",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id, name=name,
                severity=Severity.CRITICAL, passed=False,
                detail=f"Connection failed: {e}",
                expected="413 Payload Too Large or 400 Bad Request",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_malformed_content_type(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        headers: dict[str, str],
    ) -> CheckResult:
        """Send a request with wrong Content-Type and verify handling."""
        check_id = f"input.malformed_content_type_{slug}"
        name = f"Malformed Content-Type handled on {endpoint.path}"

        req_headers = {**headers, "Content-Type": "text/plain"}

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers=req_headers,
                content="not json",
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (400, 415):
                return make_result(
                    check_id=check_id, name=name, passed=True,
                    detail=f"Malformed Content-Type rejected with {code}",
                    expected="400 Bad Request or 415 Unsupported Media Type",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            elif code >= 500:
                return make_result(
                    check_id=check_id, name=name, passed=False,
                    detail=f"Server crashed ({code}) on malformed Content-Type",
                    expected="400 Bad Request or 415 Unsupported Media Type",
                    recommendation="Handle unexpected Content-Type gracefully instead of crashing.",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            elif 200 <= code < 300:
                return CheckResult(
                    check_id=check_id, name=name,
                    severity=Severity.WARNING, passed=False,
                    detail=f"Server accepted malformed Content-Type with {code}",
                    expected="400 Bad Request or 415 Unsupported Media Type",
                    recommendation="Validate Content-Type header and reject unexpected media types.",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
            else:
                # 401, 403, 404, 422 — rejected for other reasons
                return make_result(
                    check_id=check_id, name=name, passed=True,
                    detail=f"Request rejected with {code} before content-type processing",
                    expected="400 Bad Request or 415 Unsupported Media Type",
                    endpoint=endpoint.path, response_code=code, latency_ms=latency,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id, name=name,
                severity=Severity.CRITICAL, passed=False,
                detail=f"Connection failed: {e}",
                expected="400 Bad Request or 415 Unsupported Media Type",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_injection_probes(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        headers: dict[str, str],
    ) -> list[CheckResult]:
        """Send injection probe strings as query parameters."""
        results: list[CheckResult] = []

        for tag, payload in _INJECTION_PROBES:
            check_id = f"input.injection_{tag}_{slug}"
            name = f"Injection probe ({tag}) on {endpoint.path}"
            probe_path = f"{path}?probe={quote(payload)}"

            start = time.monotonic()
            try:
                response = await client.request(
                    endpoint.method, probe_path, headers=headers,
                )
                latency = round((time.monotonic() - start) * 1000, 2)
                code = response.status_code

                if code == 400:
                    results.append(make_result(
                        check_id=check_id, name=name, passed=True,
                        detail=f"Injection probe rejected with 400",
                        expected="400 Bad Request (input validation)",
                        endpoint=endpoint.path, response_code=code, latency_ms=latency,
                    ))
                elif code >= 500:
                    results.append(CheckResult(
                        check_id=check_id, name=name,
                        severity=Severity.CRITICAL, passed=False,
                        detail=f"Server crashed ({code}) on injection probe: {payload}",
                        expected="400 Bad Request (input validation)",
                        recommendation="Sanitize and validate all input. The server should never crash on malformed input.",
                        endpoint=endpoint.path, response_code=code, latency_ms=latency,
                    ))
                elif code in (401, 403, 404):
                    results.append(make_result(
                        check_id=check_id, name=name, passed=True,
                        detail=f"Request rejected with {code} before input processing",
                        expected="400 Bad Request (input validation)",
                        endpoint=endpoint.path, response_code=code, latency_ms=latency,
                    ))
                elif 200 <= code < 300:
                    results.append(CheckResult(
                        check_id=check_id, name=name,
                        severity=Severity.INFO, passed=True,
                        detail=f"Probe accepted with {code} (no crash, but input validation missing)",
                        expected="400 Bad Request (input validation)",
                        recommendation=f"Add input validation to reject suspicious query parameters.",
                        endpoint=endpoint.path, response_code=code, latency_ms=latency,
                    ))
                else:
                    results.append(make_result(
                        check_id=check_id, name=name, passed=True,
                        detail=f"Probe returned {code}",
                        expected="400 Bad Request (input validation)",
                        endpoint=endpoint.path, response_code=code, latency_ms=latency,
                    ))
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
                results.append(CheckResult(
                    check_id=check_id, name=name,
                    severity=Severity.CRITICAL, passed=False,
                    detail=f"Connection failed: {e}",
                    expected="400 Bad Request (input validation)",
                    recommendation="Verify the server is reachable.",
                    endpoint=endpoint.path,
                ))

        return results
