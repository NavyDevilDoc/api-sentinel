"""Authentication checks.

Probes protected endpoints with various token states (missing, empty,
malformed, valid) to verify correct 401/403 behavior. Also validates
that public endpoints remain accessible without authentication.
"""

from __future__ import annotations

import time
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

# Malformed token used for rejection testing
_MALFORMED_TOKEN = "not-a-real-token-sentinel-probe-xxx"


def _expect_401_result(
    check_id: str,
    name: str,
    response: httpx.Response,
    endpoint_path: str,
    latency_ms: float,
) -> CheckResult:
    """Evaluate a response that should be 401 Unauthorized.

    Returns PASS for 401, WARNING for 403 (wrong but not dangerous),
    CRITICAL for anything else (potential auth bypass).
    """
    code = response.status_code

    if code == 401:
        return make_result(
            check_id=check_id,
            name=name,
            passed=True,
            detail=f"Received 401 Unauthorized as expected",
            expected="401 Unauthorized",
            endpoint=endpoint_path,
            response_code=code,
            latency_ms=latency_ms,
        )
    elif code == 403:
        return CheckResult(
            check_id=check_id,
            name=name,
            severity=Severity.WARNING,
            passed=False,
            detail=f"Received 403 Forbidden instead of 401 Unauthorized",
            expected="401 Unauthorized",
            recommendation=(
                "Return 401 for missing/invalid credentials. "
                "Reserve 403 for valid credentials with insufficient permissions."
            ),
            endpoint=endpoint_path,
            response_code=code,
            latency_ms=latency_ms,
        )
    else:
        return make_result(
            check_id=check_id,
            name=name,
            passed=False,
            detail=f"Received {code} instead of 401 -- possible auth bypass",
            expected="401 Unauthorized",
            recommendation="Ensure all protected endpoints reject requests without valid credentials.",
            endpoint=endpoint_path,
            response_code=code,
            latency_ms=latency_ms,
        )


class AuthCheck(BaseCheck):
    """Authentication checks for protected and public endpoints."""

    check_category = "auth"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        # Resolve the primary token from environment
        try:
            token = resolve_env_var(
                config.auth.token_primary,
                description="primary API token for auth checks",
            )
        except EnvVarError as e:
            return [CheckResult(
                check_id="auth.token_resolution_error",
                name="Auth token resolution",
                severity=Severity.CRITICAL,
                passed=False,
                detail=str(e),
                expected=f"Environment variable '{config.auth.token_primary}' must be set",
                recommendation=f"Set the {config.auth.token_primary} environment variable or add it to your .env file.",
            )]

        results: list[CheckResult] = []

        for endpoint in config.endpoints:
            slug = endpoint_slug(endpoint.path)
            resolved_path = resolve_path(endpoint)

            if resolved_path is None:
                results.append(CheckResult(
                    check_id=f"auth.path_unresolvable_{slug}",
                    name=f"Path resolution for {endpoint.path}",
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Path '{endpoint.path}' has a placeholder but test_ids is empty",
                    expected="test_ids must contain at least one value for parameterized paths",
                    recommendation="Add test_ids to the endpoint configuration.",
                ))
                continue

            if endpoint.requires_auth:
                results.extend(
                    await self._check_protected_endpoint(
                        client, endpoint, resolved_path, slug, token or ""
                    )
                )
            else:
                results.extend(
                    await self._check_public_endpoint(
                        client, endpoint, resolved_path, slug, token or ""
                    )
                )

        return results

    async def _check_protected_endpoint(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        valid_token: str,
    ) -> list[CheckResult]:
        """Run all auth checks against a protected endpoint."""
        results: list[CheckResult] = []

        # 1. No token
        results.append(
            await self._check_token_rejection(
                client, endpoint, path, slug,
                check_prefix="no_token",
                name_prefix="No token",
                auth_header=None,
            )
        )

        # 2. Empty token
        results.append(
            await self._check_token_rejection(
                client, endpoint, path, slug,
                check_prefix="empty_token",
                name_prefix="Empty token",
                auth_header="Bearer ",
            )
        )

        # 3. Malformed token
        results.append(
            await self._check_token_rejection(
                client, endpoint, path, slug,
                check_prefix="malformed_token",
                name_prefix="Malformed token",
                auth_header=f"Bearer {_MALFORMED_TOKEN}",
            )
        )

        # 4. Valid token
        results.append(
            await self._check_valid_token(
                client, endpoint, path, slug, valid_token
            )
        )

        return results

    async def _check_token_rejection(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        check_prefix: str,
        name_prefix: str,
        auth_header: str | None,
    ) -> CheckResult:
        """Check that a request with invalid/missing auth is rejected with 401."""
        check_id = f"auth.{check_prefix}_{slug}"
        name = f"{name_prefix} rejected on {endpoint.path}"

        headers: dict[str, str] = {}
        if auth_header is not None:
            headers["Authorization"] = auth_header

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path, headers=headers
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            return _expect_401_result(check_id, name, response, endpoint.path, latency)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id,
                name=name,
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Connection failed: {e}",
                expected="401 Unauthorized",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_valid_token(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        valid_token: str,
    ) -> CheckResult:
        """Check that a valid token is accepted (not 401/403)."""
        check_id = f"auth.valid_token_{slug}"
        name = f"Valid token accepted on {endpoint.path}"

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers={"Authorization": f"Bearer {valid_token}"},
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (401, 403):
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=False,
                    detail=f"Valid token rejected with {code}",
                    expected="2xx or non-auth error (not 401/403)",
                    recommendation="Verify the token has correct permissions for this endpoint.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            else:
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Received {code} with valid token",
                    expected="2xx or non-auth error (not 401/403)",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id,
                name=name,
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Connection failed: {e}",
                expected="2xx or non-auth error (not 401/403)",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_public_endpoint(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        valid_token: str,
    ) -> list[CheckResult]:
        """Run auth checks against a public endpoint."""
        results: list[CheckResult] = []

        # 1. Accessible without auth
        results.append(
            await self._check_public_no_auth(client, endpoint, path, slug)
        )

        # 2. Doesn't break when auth is sent
        results.append(
            await self._check_public_with_auth(client, endpoint, path, slug, valid_token)
        )

        return results

    async def _check_public_no_auth(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
    ) -> CheckResult:
        """Check that a public endpoint is accessible without authentication."""
        check_id = f"auth.public_no_auth_{slug}"
        name = f"Public endpoint accessible: {endpoint.path}"

        start = time.monotonic()
        try:
            response = await client.request(endpoint.method, path)
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (401, 403):
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=False,
                    detail=f"Public endpoint returned {code} without auth",
                    expected="Accessible without authentication (not 401/403)",
                    recommendation="Verify endpoint configuration -- this endpoint is marked as public but requires auth.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            else:
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Public endpoint returned {code} without auth",
                    expected="Accessible without authentication (not 401/403)",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id,
                name=name,
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Connection failed: {e}",
                expected="Accessible without authentication",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_public_with_auth(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        valid_token: str,
    ) -> CheckResult:
        """Check that sending auth to a public endpoint doesn't cause a server error."""
        check_id = f"auth.public_with_auth_{slug}"
        name = f"Public endpoint stable with auth: {endpoint.path}"

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers={"Authorization": f"Bearer {valid_token}"},
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code >= 500:
                return CheckResult(
                    check_id=check_id,
                    name=name,
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Public endpoint returned {code} when auth was sent",
                    expected="Should not return 5xx when optional auth is provided",
                    recommendation="Ensure public endpoints handle unexpected Authorization headers gracefully.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            else:
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Public endpoint returned {code} with auth header",
                    expected="Should not return 5xx when optional auth is provided",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
            return CheckResult(
                check_id=check_id,
                name=name,
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Connection failed: {e}",
                expected="Should not return 5xx when optional auth is provided",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )
