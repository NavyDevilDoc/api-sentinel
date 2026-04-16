"""Security tests for rate limit checks."""

from __future__ import annotations

import itertools
from unittest.mock import patch

import httpx
import pytest
import respx

from sentinel.checks.base import Severity
from sentinel.checks.rate_limit import RateLimitCheck
from sentinel.config import SentinelConfig
from sentinel.utils.env_loader import EnvVarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _burst_responder(success_count: int, total_count: int, retry_after: str | None = "5"):
    """Create a respx side_effect callable that returns 200 for the first
    success_count requests, then 429 for the rest.

    Args:
        success_count: Number of 200 responses before rate limiting kicks in.
        total_count: Total expected requests (for documentation; not enforced).
        retry_after: Value for Retry-After header on 429 responses, or None to omit.
    """
    counter = itertools.count()

    def responder(request: httpx.Request) -> httpx.Response:
        n = next(counter)
        if n < success_count:
            return httpx.Response(200)
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return httpx.Response(429, headers=headers)

    return responder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limit_config() -> SentinelConfig:
    """Config with one rate_limit_sensitive public endpoint and one non-sensitive."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com", "timeout_seconds": 5},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [
            {
                "path": "/auth/login",
                "method": "POST",
                "requires_auth": False,
                "rate_limit_sensitive": True,
            },
            {
                "path": "/user/profile",
                "method": "GET",
                "requires_auth": True,
                "rate_limit_sensitive": False,
            },
        ],
        "checks": {
            "rate_limit": {
                "enabled": True,
                "request_burst": 10,
                "burst_window_seconds": 5,
            },
        },
    })


@pytest.fixture
def authed_sensitive_config() -> SentinelConfig:
    """Config with a rate_limit_sensitive endpoint that requires auth."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1],
                "rate_limit_sensitive": True,
            },
        ],
    })


@pytest.fixture
def no_sensitive_config() -> SentinelConfig:
    """Config with no rate_limit_sensitive endpoints."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [
            {
                "path": "/user/profile",
                "method": "GET",
                "requires_auth": True,
            },
        ],
    })


# ---------------------------------------------------------------------------
# No Sensitive Endpoints
# ---------------------------------------------------------------------------


class TestNoSensitiveEndpoints:

    @pytest.mark.asyncio
    async def test_no_sensitive_endpoints_info(
        self, no_sensitive_config: SentinelConfig
    ) -> None:
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(no_sensitive_config, client)

        assert len(results) == 1
        assert results[0].check_id == "rate_limit.no_sensitive_endpoints"
        assert results[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Token Resolution
# ---------------------------------------------------------------------------


class TestTokenResolution:

    @pytest.mark.asyncio
    async def test_token_error_when_auth_needed(
        self, authed_sensitive_config: SentinelConfig
    ) -> None:
        with patch(
            "sentinel.checks.rate_limit.resolve_env_var",
            side_effect=EnvVarError("TEST_TOKEN", "primary API token"),
        ):
            check = RateLimitCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(authed_sensitive_config, client)

        assert len(results) == 1
        assert results[0].check_id == "rate_limit.token_resolution_error"
        assert results[0].severity == Severity.CRITICAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_token_needed_for_public_endpoint(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        """Public sensitive endpoint does not require token resolution."""
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10)
        )
        # Do NOT patch resolve_env_var — it should never be called
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst_result = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst_result.passed is True


# ---------------------------------------------------------------------------
# Burst Detection
# ---------------------------------------------------------------------------


class TestBurstDetection:

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_received_pass(self, rate_limit_config: SentinelConfig) -> None:
        """Mixed 200/429 responses means rate limiting is enforced."""
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10)
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst.passed is True
        assert "429" in burst.detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_200_no_rate_limiting_critical(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        """All 200 responses means no rate limiting."""
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(10, 10)  # all success
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst.passed is False
        assert burst.severity == Severity.CRITICAL
        assert "no rate limiting" in burst.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_first_request_429_pass(self, rate_limit_config: SentinelConfig) -> None:
        """Even first request returning 429 is a pass (aggressive rate limiting)."""
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(0, 10)  # all 429
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_429_errors_critical(self, rate_limit_config: SentinelConfig) -> None:
        """403/500 responses without any 429 means improper rate limiting."""
        counter = itertools.count()

        def mixed_errors(request: httpx.Request) -> httpx.Response:
            n = next(counter)
            if n % 2 == 0:
                return httpx.Response(200)
            return httpx.Response(403)

        respx.post("https://api.test.com/auth/login").mock(side_effect=mixed_errors)
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst.passed is False
        assert burst.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Retry-After Header
# ---------------------------------------------------------------------------


class TestRetryAfterHeader:

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_with_retry_after_pass(self, rate_limit_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10, retry_after="30")
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        retry = next(r for r in results if r.check_id == "rate_limit.retry_after_auth_login")
        assert retry.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_without_retry_after_warning(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10, retry_after=None)  # no Retry-After
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        retry = next(r for r in results if r.check_id == "rate_limit.retry_after_auth_login")
        assert retry.passed is False
        assert retry.severity == Severity.WARNING

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_retry_after_check_when_no_429(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        """If no 429 received, no retry_after check is emitted."""
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(10, 10)  # all 200
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        retry_results = [r for r in results if "retry_after" in r.check_id]
        assert len(retry_results) == 0


# ---------------------------------------------------------------------------
# Connection Errors
# ---------------------------------------------------------------------------


class TestConnectionErrors:

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_connection_errors_critical(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        burst = next(r for r in results if r.check_id == "rate_limit.burst_auth_login")
        assert burst.passed is False
        assert burst.severity == Severity.CRITICAL
        assert "connection errors" in burst.detail.lower()


# ---------------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------------


class TestPathResolution:

    @pytest.mark.asyncio
    async def test_unresolvable_path_warning(self) -> None:
        config = SentinelConfig.model_validate({
            "meta": {"project": "test", "base_url": "https://api.test.com"},
            "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
            "endpoints": [
                {
                    "path": "/resource/{id}",
                    "method": "GET",
                    "requires_auth": False,
                    "test_ids": [],
                    "rate_limit_sensitive": True,
                },
            ],
        })
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(config, client)

        assert len(results) == 1
        assert results[0].check_id == "rate_limit.path_unresolvable_resource_id"
        assert results[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Auth Headers
# ---------------------------------------------------------------------------


class TestAuthHeaders:

    @pytest.mark.asyncio
    @respx.mock
    async def test_auth_header_included_when_required(
        self, authed_sensitive_config: SentinelConfig
    ) -> None:
        """Burst requests to requires_auth endpoint include Authorization header."""
        received_headers: list[dict] = []

        def capture_headers(request: httpx.Request) -> httpx.Response:
            received_headers.append(dict(request.headers))
            return httpx.Response(429, headers={"Retry-After": "5"})

        respx.get("https://api.test.com/resource/1").mock(side_effect=capture_headers)

        with patch("sentinel.checks.rate_limit.resolve_env_var", return_value="my-secret-token"):
            check = RateLimitCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                await check.run(authed_sensitive_config, client)

        assert len(received_headers) > 0
        assert all("authorization" in h for h in received_headers)

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_auth_header_for_public_endpoint(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        """Burst requests to public endpoint omit Authorization header."""
        received_headers: list[dict] = []

        def capture_headers(request: httpx.Request) -> httpx.Response:
            received_headers.append(dict(request.headers))
            return httpx.Response(429, headers={"Retry-After": "5"})

        respx.post("https://api.test.com/auth/login").mock(side_effect=capture_headers)

        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check.run(rate_limit_config, client)

        assert len(received_headers) > 0
        assert all("authorization" not in h for h in received_headers)


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_burst_count_matches_config(
        self, rate_limit_config: SentinelConfig
    ) -> None:
        """Exactly request_burst requests are sent."""
        route = respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10)
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check.run(rate_limit_config, client)

        assert route.call_count == 10  # request_burst=10

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_ids_unique(self, rate_limit_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(
            side_effect=_burst_responder(5, 10)
        )
        check = RateLimitCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(rate_limit_config, client)

        check_ids = [r.check_id for r in results]
        assert len(check_ids) == len(set(check_ids))
