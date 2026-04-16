"""Rate limiting checks.

Sends a burst of concurrent async requests to endpoints flagged as
rate_limit_sensitive and validates that the API enforces rate limits
(429 response with Retry-After header).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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


class RateLimitCheck(BaseCheck):
    """Rate limiting checks via concurrent burst requests."""

    check_category = "rate_limit"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        # Filter to rate_limit_sensitive endpoints only
        sensitive = [ep for ep in config.endpoints if ep.rate_limit_sensitive]

        if not sensitive:
            return [CheckResult(
                check_id="rate_limit.no_sensitive_endpoints",
                name="Rate limit sensitive endpoints",
                severity=Severity.INFO,
                passed=True,
                detail="No endpoints flagged as rate_limit_sensitive",
                expected="At least one endpoint with rate_limit_sensitive=true for burst testing",
                recommendation="Add rate_limit_sensitive: true to endpoints that should be rate-limited.",
            )]

        # Resolve auth token if any sensitive endpoint requires auth
        token: str | None = None
        needs_auth = any(ep.requires_auth for ep in sensitive)
        if needs_auth:
            try:
                token = resolve_env_var(
                    config.auth.token_primary,
                    description="primary API token for rate limit checks",
                )
            except EnvVarError as e:
                return [CheckResult(
                    check_id="rate_limit.token_resolution_error",
                    name="Rate limit token resolution",
                    severity=Severity.CRITICAL,
                    passed=False,
                    detail=str(e),
                    expected=f"Environment variable '{config.auth.token_primary}' must be set",
                    recommendation=f"Set the {config.auth.token_primary} environment variable or add it to your .env file.",
                )]

        results: list[CheckResult] = []
        burst_count = config.checks.rate_limit.request_burst
        timeout = config.checks.rate_limit.burst_window_seconds

        for ep in sensitive:
            slug = endpoint_slug(ep.path)
            resolved = resolve_path(ep)

            if resolved is None:
                results.append(CheckResult(
                    check_id=f"rate_limit.path_unresolvable_{slug}",
                    name=f"Path resolution for {ep.path}",
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Path '{ep.path}' has a placeholder but test_ids is empty",
                    expected="test_ids must contain at least one value for parameterized paths",
                    recommendation="Add test_ids to the endpoint configuration.",
                ))
                continue

            results.extend(
                await self._burst_endpoint(client, ep, resolved, slug, token, burst_count, timeout)
            )

        return results

    async def _burst_endpoint(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        token: str | None,
        burst_count: int,
        timeout: float,
    ) -> list[CheckResult]:
        """Fire a burst of concurrent requests and analyze rate limit behavior."""
        headers: dict[str, str] = {}
        if endpoint.requires_auth and token:
            headers["Authorization"] = f"Bearer {token}"

        # Fire all requests concurrently
        tasks = [
            asyncio.create_task(
                client.request(endpoint.method, path, headers=headers)
            )
            for _ in range(burst_count)
        ]

        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Cancel any still-pending tasks
        for task in pending:
            task.cancel()

        # Collect responses and exceptions
        responses: list[httpx.Response] = []
        exceptions: list[BaseException] = []
        for task in done:
            exc = task.exception()
            if exc is not None:
                exceptions.append(exc)
            else:
                responses.append(task.result())

        results: list[CheckResult] = []

        # If nothing completed, report connectivity failure
        if not responses and not exceptions:
            results.append(CheckResult(
                check_id=f"rate_limit.burst_{slug}",
                name=f"Rate limit enforcement on {endpoint.path}",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"All {burst_count} burst requests timed out within {timeout}s",
                expected="At least one 429 Too Many Requests response",
                recommendation="Verify the server is reachable and responsive.",
                endpoint=endpoint.path,
            ))
            return results

        if not responses and exceptions:
            results.append(CheckResult(
                check_id=f"rate_limit.burst_{slug}",
                name=f"Rate limit enforcement on {endpoint.path}",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"All {len(exceptions)} burst requests failed with connection errors",
                expected="At least one 429 Too Many Requests response",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            ))
            return results

        # Analyze responses
        responses_429 = [r for r in responses if r.status_code == 429]
        got_429 = len(responses_429) > 0
        total_completed = len(responses)

        # Check 1: Was rate limiting enforced?
        if got_429:
            results.append(make_result(
                check_id=f"rate_limit.burst_{slug}",
                name=f"Rate limit enforcement on {endpoint.path}",
                passed=True,
                detail=(
                    f"{len(responses_429)} of {total_completed} requests received 429 "
                    f"({burst_count} sent)"
                ),
                expected="At least one 429 Too Many Requests response",
                endpoint=endpoint.path,
            ))
        else:
            all_success = all(200 <= r.status_code < 300 for r in responses)
            if all_success:
                detail = (
                    f"All {total_completed} requests returned 2xx -- "
                    f"no rate limiting detected ({burst_count} sent)"
                )
            else:
                status_codes = sorted({r.status_code for r in responses})
                detail = (
                    f"No 429 responses received. Status codes: {status_codes} "
                    f"({burst_count} sent)"
                )
            results.append(make_result(
                check_id=f"rate_limit.burst_{slug}",
                name=f"Rate limit enforcement on {endpoint.path}",
                passed=False,
                detail=detail,
                expected="At least one 429 Too Many Requests response",
                recommendation="Implement rate limiting with HTTP 429 status code on this endpoint.",
                endpoint=endpoint.path,
            ))

        # Check 2: Do 429 responses include Retry-After? (only if 429 was received)
        if got_429:
            with_retry_after = sum(
                1 for r in responses_429 if r.headers.get("retry-after")
            )
            all_have_retry_after = with_retry_after == len(responses_429)

            if all_have_retry_after:
                results.append(make_result(
                    check_id=f"rate_limit.retry_after_{slug}",
                    name=f"Retry-After header on {endpoint.path}",
                    passed=True,
                    detail=f"All {len(responses_429)} 429 responses include Retry-After header",
                    expected="Retry-After header present on 429 responses",
                    endpoint=endpoint.path,
                ))
            else:
                missing = len(responses_429) - with_retry_after
                results.append(CheckResult(
                    check_id=f"rate_limit.retry_after_{slug}",
                    name=f"Retry-After header on {endpoint.path}",
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"{missing} of {len(responses_429)} 429 responses missing Retry-After header",
                    expected="Retry-After header present on 429 responses",
                    recommendation="Include Retry-After header in 429 responses to help clients back off appropriately.",
                    endpoint=endpoint.path,
                ))

        return results
