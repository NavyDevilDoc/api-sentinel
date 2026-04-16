"""Security tests for authentication checks."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from sentinel.checks.auth import AuthCheck
from sentinel.checks.base import Severity, endpoint_slug, resolve_path
from sentinel.config import EndpointConfig, SentinelConfig
from sentinel.utils.env_loader import EnvVarError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_config() -> SentinelConfig:
    """Config with protected and public endpoints."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1, 2, 3],
                "owned_by": "token_primary",
            },
            {
                "path": "/user/profile",
                "method": "GET",
                "requires_auth": True,
            },
            {
                "path": "/auth/login",
                "method": "POST",
                "requires_auth": False,
            },
        ],
    })


@pytest.fixture
def empty_endpoints_config() -> SentinelConfig:
    """Config with no endpoints."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [],
    })


@pytest.fixture
def unresolvable_path_config() -> SentinelConfig:
    """Config with a parameterized path but empty test_ids."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TEST_TOKEN", "token_secondary": "TEST_TOKEN_B"},
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [],
            },
        ],
    })


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


class TestHelpers:

    def testendpoint_slug(self) -> None:
        assert endpoint_slug("/resource/{id}") == "resource_id"
        assert endpoint_slug("/user/profile") == "user_profile"
        assert endpoint_slug("/auth/login") == "auth_login"
        assert endpoint_slug("/a/b/c") == "a_b_c"

    def testresolve_path_with_id(self) -> None:
        ep = EndpointConfig(path="/resource/{id}", test_ids=[42])
        assert resolve_path(ep) == "/resource/42"

    def testresolve_path_no_placeholder(self) -> None:
        ep = EndpointConfig(path="/user/profile")
        assert resolve_path(ep) == "/user/profile"

    def testresolve_path_placeholder_no_ids(self) -> None:
        ep = EndpointConfig(path="/resource/{id}", test_ids=[])
        assert resolve_path(ep) is None

    def testresolve_path_string_id(self) -> None:
        ep = EndpointConfig(path="/resource/{slug}", test_ids=["abc"])
        assert resolve_path(ep) == "/resource/abc"


# ---------------------------------------------------------------------------
# Token Resolution
# ---------------------------------------------------------------------------


class TestTokenResolution:

    @pytest.mark.asyncio
    async def test_missing_token_env_var(self, auth_config: SentinelConfig) -> None:
        with patch(
            "sentinel.checks.auth.resolve_env_var",
            side_effect=EnvVarError("TEST_TOKEN", "primary API token"),
        ):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(auth_config, client)

        assert len(results) == 1
        assert results[0].check_id == "auth.token_resolution_error"
        assert results[0].severity == Severity.CRITICAL
        assert "TEST_TOKEN" in results[0].detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_resolved_successfully(self, auth_config: SentinelConfig) -> None:
        """When token resolves, no token error result is produced."""
        # Mock all endpoints to return appropriate responses
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        respx.get("https://api.test.com/user/profile").mock(return_value=httpx.Response(401))
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(200))

        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(auth_config, client)

        error_results = [r for r in results if r.check_id == "auth.token_resolution_error"]
        assert len(error_results) == 0


# ---------------------------------------------------------------------------
# Path Resolution in Context
# ---------------------------------------------------------------------------


class TestPathResolutionInRun:

    @pytest.mark.asyncio
    async def test_unresolvable_path_warning(
        self, unresolvable_path_config: SentinelConfig
    ) -> None:
        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(unresolvable_path_config, client)

        assert len(results) == 1
        assert results[0].check_id == "auth.path_unresolvable_resource_id"
        assert results[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Protected Endpoint — Token Rejection (no/empty/malformed)
# ---------------------------------------------------------------------------


class TestProtectedEndpointRejection:

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_token_401_pass(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                result = await check._check_token_rejection(
                    client, auth_config.endpoints[0], "/resource/1", "resource_id",
                    check_prefix="no_token", name_prefix="No token", auth_header=None,
                )
        assert result.passed is True
        assert result.check_id == "auth.no_token_resource_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_token_200_critical(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="no_token", name_prefix="No token", auth_header=None,
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL
        assert "auth bypass" in result.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_token_403_warning(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(403))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="no_token", name_prefix="No token", auth_header=None,
            )
        assert result.passed is False
        assert result.severity == Severity.WARNING
        assert "403" in result.detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_token_401_pass(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="empty_token", name_prefix="Empty token",
                auth_header="Bearer ",
            )
        assert result.passed is True
        assert result.check_id == "auth.empty_token_resource_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_token_401_pass(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="malformed_token", name_prefix="Malformed token",
                auth_header="Bearer not-a-real-token-sentinel-probe-xxx",
            )
        assert result.passed is True
        assert result.check_id == "auth.malformed_token_resource_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_connection_error_critical(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="no_token", name_prefix="No token", auth_header=None,
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL
        assert "Connection failed" in result.detail


# ---------------------------------------------------------------------------
# Protected Endpoint — Valid Token
# ---------------------------------------------------------------------------


class TestProtectedEndpointValidToken:

    @pytest.mark.asyncio
    @respx.mock
    async def test_valid_token_200_pass(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_valid_token(
                client, auth_config.endpoints[0], "/resource/1", "resource_id", "fake-token",
            )
        assert result.passed is True
        assert result.check_id == "auth.valid_token_resource_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_valid_token_404_pass(self, auth_config: SentinelConfig) -> None:
        """404 is acceptable -- auth worked, resource just doesn't exist."""
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(404))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_valid_token(
                client, auth_config.endpoints[0], "/resource/1", "resource_id", "fake-token",
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_valid_token_401_critical(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_valid_token(
                client, auth_config.endpoints[0], "/resource/1", "resource_id", "fake-token",
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_valid_token_403_critical(self, auth_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(403))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_valid_token(
                client, auth_config.endpoints[0], "/resource/1", "resource_id", "fake-token",
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Public Endpoint
# ---------------------------------------------------------------------------


class TestPublicEndpoint:

    @pytest.mark.asyncio
    @respx.mock
    async def test_public_no_auth_accessible(self, auth_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(200))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_public_no_auth(
                client, auth_config.endpoints[2], "/auth/login", "auth_login",
            )
        assert result.passed is True
        assert result.check_id == "auth.public_no_auth_auth_login"

    @pytest.mark.asyncio
    @respx.mock
    async def test_public_no_auth_blocked(self, auth_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(401))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_public_no_auth(
                client, auth_config.endpoints[2], "/auth/login", "auth_login",
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_public_with_auth_ok(self, auth_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(200))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_public_with_auth(
                client, auth_config.endpoints[2], "/auth/login", "auth_login", "fake-token",
            )
        assert result.passed is True
        assert result.check_id == "auth.public_with_auth_auth_login"

    @pytest.mark.asyncio
    @respx.mock
    async def test_public_with_auth_500_warning(self, auth_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(500))
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_public_with_auth(
                client, auth_config.endpoints[2], "/auth/login", "auth_login", "fake-token",
            )
        assert result.passed is False
        assert result.severity == Severity.WARNING


# ---------------------------------------------------------------------------
# HTTP Method Dispatch
# ---------------------------------------------------------------------------


class TestMethodDispatch:

    @pytest.mark.asyncio
    @respx.mock
    async def test_post_endpoint_uses_post(self, auth_config: SentinelConfig) -> None:
        """POST endpoint dispatches a POST request."""
        route = respx.post("https://api.test.com/auth/login").mock(
            return_value=httpx.Response(200)
        )
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check._check_public_no_auth(
                client, auth_config.endpoints[2], "/auth/login", "auth_login",
            )
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_endpoint_uses_get(self, auth_config: SentinelConfig) -> None:
        """GET endpoint dispatches a GET request."""
        route = respx.get("https://api.test.com/resource/1").mock(
            return_value=httpx.Response(401)
        )
        check = AuthCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check._check_token_rejection(
                client, auth_config.endpoints[0], "/resource/1", "resource_id",
                check_prefix="no_token", name_prefix="No token", auth_header=None,
            )
        assert route.called


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullAuthRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_full_run_all_endpoints(self, auth_config: SentinelConfig) -> None:
        """Full run produces correct number of results for all endpoints."""
        # Protected: /resource/1 (GET) - 4 checks
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        # Protected: /user/profile (GET) - 4 checks
        respx.get("https://api.test.com/user/profile").mock(return_value=httpx.Response(401))
        # Public: /auth/login (POST) - 2 checks
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(200))

        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(auth_config, client)

        # 2 protected * 4 checks + 1 public * 2 checks = 10
        assert len(results) == 10

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_ids_unique(self, auth_config: SentinelConfig) -> None:
        """All check_ids in a full run are unique."""
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        respx.get("https://api.test.com/user/profile").mock(return_value=httpx.Response(401))
        respx.post("https://api.test.com/auth/login").mock(return_value=httpx.Response(200))

        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(auth_config, client)

        check_ids = [r.check_id for r in results]
        assert len(check_ids) == len(set(check_ids)), f"Duplicate check_ids: {check_ids}"

    @pytest.mark.asyncio
    async def test_empty_endpoints_no_results(
        self, empty_endpoints_config: SentinelConfig
    ) -> None:
        with patch("sentinel.checks.auth.resolve_env_var", return_value="fake-token"):
            check = AuthCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(empty_endpoints_config, client)

        assert len(results) == 0
