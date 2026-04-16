"""Security tests for authorization (BOLA) checks."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from sentinel.checks.authorization import AuthorizationCheck
from sentinel.checks.base import Severity
from sentinel.config import SentinelConfig
from sentinel.utils.env_loader import EnvVarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_resolve(token_map: dict):
    """Create a side_effect for resolve_env_var that returns values from a dict."""
    def _resolve(var_name, **kwargs):
        if var_name in token_map:
            return token_map[var_name]
        raise EnvVarError(var_name)
    return _resolve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bola_config() -> SentinelConfig:
    """Config with BOLA-testable endpoints."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1, 2, 3],
                "owned_by": "token_primary",
            },
            {
                "path": "/other/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [10],
                "owned_by": "token_secondary",
            },
        ],
    })


@pytest.fixture
def no_bola_config() -> SentinelConfig:
    """Config with no endpoints having owned_by set."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
        "endpoints": [
            {"path": "/health", "method": "GET", "requires_auth": False},
        ],
    })


@pytest.fixture
def single_bola_config() -> SentinelConfig:
    """Config with a single BOLA endpoint for focused tests."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1],
                "owned_by": "token_primary",
            },
        ],
    })


MOCK_TOKENS = _mock_resolve({"TOK_A": "token-a-value", "TOK_B": "token-b-value"})


# ---------------------------------------------------------------------------
# Token Resolution
# ---------------------------------------------------------------------------


class TestTokenResolution:

    @pytest.mark.asyncio
    async def test_missing_primary_token(self, bola_config: SentinelConfig) -> None:
        with patch(
            "sentinel.checks.authorization.resolve_env_var",
            side_effect=EnvVarError("TOK_A", "primary token"),
        ):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(bola_config, client)

        assert len(results) == 1
        assert results[0].check_id == "authorization.token_resolution_error"
        assert results[0].severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_missing_secondary_token(self, bola_config: SentinelConfig) -> None:
        def _resolve(var_name, **kwargs):
            if var_name == "TOK_A":
                return "token-a-value"
            raise EnvVarError("TOK_B", "secondary token")

        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=_resolve):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(bola_config, client)

        assert len(results) == 1
        assert results[0].check_id == "authorization.token_resolution_error"
        assert "TOK_B" in results[0].detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_both_tokens_resolved(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(single_bola_config, client)

        error_results = [r for r in results if "token_resolution" in r.check_id]
        assert len(error_results) == 0


# ---------------------------------------------------------------------------
# Endpoint Filtering
# ---------------------------------------------------------------------------


class TestEndpointFiltering:

    @pytest.mark.asyncio
    async def test_no_bola_endpoints_info(self, no_bola_config: SentinelConfig) -> None:
        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(no_bola_config, client)

        assert len(results) == 1
        assert results[0].check_id == "authorization.no_bola_endpoints"
        assert results[0].severity == Severity.INFO

    @pytest.mark.asyncio
    async def test_unresolvable_path_warning(self) -> None:
        config = SentinelConfig.model_validate({
            "meta": {"project": "test", "base_url": "https://api.test.com"},
            "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
            "endpoints": [
                {"path": "/resource/{id}", "method": "GET", "requires_auth": True,
                 "test_ids": [], "owned_by": "token_primary"},
            ],
        })
        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(config, client)

        assert len(results) == 1
        assert results[0].check_id == "authorization.path_unresolvable_resource_id"
        assert results[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Owner Access
# ---------------------------------------------------------------------------


class TestOwnerAccess:

    @pytest.mark.asyncio
    @respx.mock
    async def test_owner_200_pass(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_owner_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-a"
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_owner_404_pass(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(404))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_owner_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-a"
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_owner_403_critical(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(403))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_owner_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-a"
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_owner_connection_error(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_owner_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-a"
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Cross-User Access
# ---------------------------------------------------------------------------


class TestCrossUserAccess:

    @pytest.mark.asyncio
    @respx.mock
    async def test_cross_user_403_pass(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(403))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_cross_user_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-b"
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_cross_user_401_pass(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(401))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_cross_user_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-b"
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_cross_user_404_pass(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(404))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_cross_user_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-b"
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_cross_user_200_critical_bola(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_cross_user_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-b"
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL
        assert "BOLA" in result.detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_cross_user_500_warning(self, single_bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(500))
        check = AuthorizationCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_cross_user_access(
                client, single_bola_config.endpoints[0], "/resource/1", "resource_id", "token-b"
            )
        assert result.passed is False
        assert result.severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Token Mapping
# ---------------------------------------------------------------------------


class TestTokenMapping:

    @pytest.mark.asyncio
    @respx.mock
    async def test_owned_by_primary_uses_secondary_for_cross(
        self, single_bola_config: SentinelConfig
    ) -> None:
        """owned_by=token_primary means cross-user probe uses secondary token."""
        received_auth: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            received_auth.append(request.headers.get("authorization", ""))
            return httpx.Response(403)

        respx.get("https://api.test.com/resource/1").mock(side_effect=capture)

        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(single_bola_config, client)

        # First call = owner (token-a-value), second = cross-user (token-b-value)
        assert "token-a-value" in received_auth[0]
        assert "token-b-value" in received_auth[1]

    @pytest.mark.asyncio
    @respx.mock
    async def test_owned_by_secondary_uses_primary_for_cross(self) -> None:
        """owned_by=token_secondary means cross-user probe uses primary token."""
        config = SentinelConfig.model_validate({
            "meta": {"project": "test", "base_url": "https://api.test.com"},
            "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
            "endpoints": [
                {"path": "/other/{id}", "method": "GET", "requires_auth": True,
                 "test_ids": [10], "owned_by": "token_secondary"},
            ],
        })

        received_auth: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            received_auth.append(request.headers.get("authorization", ""))
            return httpx.Response(403)

        respx.get("https://api.test.com/other/10").mock(side_effect=capture)

        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                await check.run(config, client)

        # Owner = token-b-value, cross = token-a-value
        assert "token-b-value" in received_auth[0]
        assert "token-a-value" in received_auth[1]


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_full_run_result_count(self, bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        respx.get("https://api.test.com/other/10").mock(return_value=httpx.Response(403))

        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(bola_config, client)

        # 2 endpoints * 2 checks = 4 results
        assert len(results) == 4

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_ids_unique(self, bola_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/resource/1").mock(return_value=httpx.Response(200))
        respx.get("https://api.test.com/other/10").mock(return_value=httpx.Response(403))

        with patch("sentinel.checks.authorization.resolve_env_var", side_effect=MOCK_TOKENS):
            check = AuthorizationCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(bola_config, client)

        check_ids = [r.check_id for r in results]
        assert len(check_ids) == len(set(check_ids))
