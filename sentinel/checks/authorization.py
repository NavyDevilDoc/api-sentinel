"""Authorization (BOLA) checks.

Tests whether User A's token can access resources owned by User B.
This is the most impactful check category — a confirmed BOLA finding
means cross-user data access is possible.

Requires token_secondary to be configured.
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


class AuthorizationCheck(BaseCheck):
    """Cross-user authorization (BOLA) checks."""

    check_category = "authorization"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        # Resolve both tokens
        tokens = self._resolve_tokens(config)
        if isinstance(tokens, CheckResult):
            return [tokens]
        primary_value, secondary_value = tokens

        token_values = {
            "token_primary": primary_value,
            "token_secondary": secondary_value,
        }

        # Filter to endpoints with owned_by set and requires_auth
        bola_endpoints = [
            ep for ep in config.endpoints
            if ep.owned_by is not None and ep.requires_auth
        ]

        if not bola_endpoints:
            return [CheckResult(
                check_id="authorization.no_bola_endpoints",
                name="BOLA endpoint configuration",
                severity=Severity.INFO,
                passed=True,
                detail="No endpoints configured with owned_by for BOLA testing",
                expected="At least one endpoint with owned_by and requires_auth for cross-user testing",
                recommendation="Add owned_by to endpoint configurations to enable BOLA checks.",
            )]

        results: list[CheckResult] = []

        for ep in bola_endpoints:
            slug = endpoint_slug(ep.path)
            resolved = resolve_path(ep)

            if resolved is None:
                results.append(CheckResult(
                    check_id=f"authorization.path_unresolvable_{slug}",
                    name=f"Path resolution for {ep.path}",
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Path '{ep.path}' has a placeholder but test_ids is empty",
                    expected="test_ids must contain at least one value for parameterized paths",
                    recommendation="Add test_ids to the endpoint configuration.",
                ))
                continue

            # Determine owner and other tokens
            owner_token = token_values.get(ep.owned_by, primary_value)
            other_token = (
                secondary_value if owner_token == primary_value else primary_value
            )

            results.append(
                await self._check_owner_access(client, ep, resolved, slug, owner_token)
            )
            results.append(
                await self._check_cross_user_access(client, ep, resolved, slug, other_token)
            )

        return results

    def _resolve_tokens(
        self, config: SentinelConfig
    ) -> tuple[str, str] | CheckResult:
        """Resolve both primary and secondary tokens.

        Returns (primary_value, secondary_value) or a CRITICAL CheckResult on failure.
        """
        try:
            primary = resolve_env_var(
                config.auth.token_primary,
                description="primary API token for BOLA checks",
            )
        except EnvVarError as e:
            return CheckResult(
                check_id="authorization.token_resolution_error",
                name="Authorization token resolution",
                severity=Severity.CRITICAL,
                passed=False,
                detail=str(e),
                expected=f"Environment variable '{config.auth.token_primary}' must be set",
                recommendation=f"Set the {config.auth.token_primary} environment variable.",
            )

        if config.auth.token_secondary is None:
            return CheckResult(
                check_id="authorization.token_resolution_error",
                name="Authorization token resolution",
                severity=Severity.CRITICAL,
                passed=False,
                detail="token_secondary is not configured",
                expected="auth.token_secondary must be set for BOLA checks",
                recommendation="Add token_secondary to the auth section of your config.",
            )

        try:
            secondary = resolve_env_var(
                config.auth.token_secondary,
                description="secondary API token for BOLA checks",
            )
        except EnvVarError as e:
            return CheckResult(
                check_id="authorization.token_resolution_error",
                name="Authorization token resolution",
                severity=Severity.CRITICAL,
                passed=False,
                detail=str(e),
                expected=f"Environment variable '{config.auth.token_secondary}' must be set",
                recommendation=f"Set the {config.auth.token_secondary} environment variable.",
            )

        return (primary or "", secondary or "")

    async def _check_owner_access(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        owner_token: str,
    ) -> CheckResult:
        """Verify the resource owner can access their own resource."""
        check_id = f"authorization.owner_access_{slug}"
        name = f"Owner access on {endpoint.path}"

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (401, 403):
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=False,
                    detail=f"Owner received {code} on their own resource",
                    expected="2xx or 404 (owner should access own resource)",
                    recommendation="Verify token permissions -- the owner should have access to their own resources.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            else:
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Owner received {code} on their own resource",
                    expected="2xx or 404 (owner should access own resource)",
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
                expected="2xx or 404 (owner should access own resource)",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )

    async def _check_cross_user_access(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        path: str,
        slug: str,
        other_token: str,
    ) -> CheckResult:
        """Probe whether another user can access a resource they don't own."""
        check_id = f"authorization.cross_user_access_{slug}"
        name = f"Cross-user access on {endpoint.path}"

        start = time.monotonic()
        try:
            response = await client.request(
                endpoint.method, path,
                headers={"Authorization": f"Bearer {other_token}"},
            )
            latency = round((time.monotonic() - start) * 1000, 2)
            code = response.status_code

            if code in (401, 403, 404):
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Cross-user access correctly denied with {code}",
                    expected="403/401/404 (other user denied access)",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            elif 200 <= code < 300:
                return CheckResult(
                    check_id=check_id,
                    name=name,
                    severity=Severity.CRITICAL,
                    passed=False,
                    detail=f"BOLA DETECTED: Other user received {code} on resource they do not own",
                    expected="403/401/404 (other user denied access)",
                    recommendation="Implement object-level authorization -- verify the requesting user owns the resource.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            elif code >= 500:
                return CheckResult(
                    check_id=check_id,
                    name=name,
                    severity=Severity.WARNING,
                    passed=False,
                    detail=f"Server returned {code} on cross-user access probe",
                    expected="403/401/404 (other user denied access)",
                    recommendation="Investigate server error on cross-user access attempt.",
                    endpoint=endpoint.path,
                    response_code=code,
                    latency_ms=latency,
                )
            else:
                return make_result(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    detail=f"Cross-user request returned {code}",
                    expected="403/401/404 (other user denied access)",
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
                expected="403/401/404 (other user denied access)",
                recommendation="Verify the server is reachable.",
                endpoint=endpoint.path,
            )
