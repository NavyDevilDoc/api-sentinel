"""Security tests for input handling checks."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from sentinel.checks.base import Severity
from sentinel.checks.input_handling import InputHandlingCheck
from sentinel.config import SentinelConfig
from sentinel.utils.env_loader import EnvVarError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def input_config() -> SentinelConfig:
    """Config with POST and GET endpoints for input testing."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK_B"},
        "endpoints": [
            {
                "path": "/resource",
                "method": "POST",
                "requires_auth": True,
            },
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1],
            },
        ],
        "checks": {
            "input_handling": {"enabled": True, "max_payload_kb": 1},
        },
    })


@pytest.fixture
def public_get_config() -> SentinelConfig:
    """Config with a single public GET endpoint."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK_B"},
        "endpoints": [
            {"path": "/health", "method": "GET", "requires_auth": False},
        ],
        "checks": {
            "input_handling": {"enabled": True, "max_payload_kb": 1},
        },
    })


@pytest.fixture
def post_only_config() -> SentinelConfig:
    """Config with a single POST endpoint for focused tests."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK_B"},
        "endpoints": [
            {"path": "/resource", "method": "POST", "requires_auth": True},
        ],
        "checks": {
            "input_handling": {"enabled": True, "max_payload_kb": 1},
        },
    })


# ---------------------------------------------------------------------------
# Token Resolution
# ---------------------------------------------------------------------------


class TestTokenResolution:

    @pytest.mark.asyncio
    async def test_missing_token(self, input_config: SentinelConfig) -> None:
        with patch(
            "sentinel.checks.input_handling.resolve_env_var",
            side_effect=EnvVarError("TOK"),
        ):
            check = InputHandlingCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(input_config, client)

        assert len(results) == 1
        assert results[0].check_id == "input.token_resolution_error"
        assert results[0].severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Oversized Payload
# ---------------------------------------------------------------------------


class TestOversizedPayload:

    @pytest.mark.asyncio
    @respx.mock
    async def test_oversized_413_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(413))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_oversized_payload(
                client, post_only_config.endpoints[0], "/resource", "resource",
                {"Authorization": "Bearer tok"}, 1,
            )
        assert result.passed is True
        assert result.response_code == 413

    @pytest.mark.asyncio
    @respx.mock
    async def test_oversized_400_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(400))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_oversized_payload(
                client, post_only_config.endpoints[0], "/resource", "resource",
                {}, 1,
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_oversized_200_critical(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(200))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_oversized_payload(
                client, post_only_config.endpoints[0], "/resource", "resource",
                {}, 1,
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    @respx.mock
    async def test_oversized_500_warning(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(500))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_oversized_payload(
                client, post_only_config.endpoints[0], "/resource", "resource",
                {}, 1,
            )
        assert result.passed is False
        assert result.severity == Severity.WARNING

    @pytest.mark.asyncio
    @respx.mock
    async def test_oversized_payload_size(self, post_only_config: SentinelConfig) -> None:
        """Verify the payload is larger than max_payload_kb."""
        received_sizes: list[int] = []

        def capture(request: httpx.Request) -> httpx.Response:
            received_sizes.append(len(request.content))
            return httpx.Response(413)

        respx.post("https://api.test.com/resource").mock(side_effect=capture)
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check._check_oversized_payload(
                client, post_only_config.endpoints[0], "/resource", "resource",
                {}, 1,
            )

        assert received_sizes[0] > 1 * 1024  # > max_payload_kb


# ---------------------------------------------------------------------------
# Malformed Content-Type
# ---------------------------------------------------------------------------


class TestMalformedContentType:

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_415_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(415))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_malformed_content_type(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_400_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(400))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_malformed_content_type(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert result.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_200_warning(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(200))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_malformed_content_type(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert result.passed is False
        assert result.severity == Severity.WARNING

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_500_critical(self, post_only_config: SentinelConfig) -> None:
        respx.post("https://api.test.com/resource").mock(return_value=httpx.Response(500))
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_malformed_content_type(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert result.passed is False
        assert result.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Injection Probes
# ---------------------------------------------------------------------------


class TestInjectionProbes:

    @pytest.mark.asyncio
    @respx.mock
    async def test_injection_400_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(400)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert all(r.passed for r in results)
        assert all(r.severity == Severity.PASS for r in results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_injection_200_info(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(200)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert all(r.severity == Severity.INFO for r in results)
        # INFO results still have passed=True (no crash)
        assert all(r.passed for r in results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_injection_500_critical(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(500)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert all(not r.passed for r in results)
        assert all(r.severity == Severity.CRITICAL for r in results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_injection_401_pass(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(401)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert all(r.passed for r in results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_four_probes_produced(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(400)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, post_only_config.endpoints[0], "/resource", "resource", {},
            )
        assert len(results) == 4
        tags = {r.check_id.split("_")[1] for r in results}  # injection_{tag}_{slug}
        assert tags == {"sql", "xss", "ssti", "path"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_injection_on_get_endpoint(self, input_config: SentinelConfig) -> None:
        """GET endpoints get injection probes in query params."""
        respx.get(url__startswith="https://api.test.com/resource/1").mock(
            return_value=httpx.Response(400)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check._check_injection_probes(
                client, input_config.endpoints[1], "/resource/1", "resource_id", {},
            )
        assert len(results) == 4


# ---------------------------------------------------------------------------
# Endpoint Selection
# ---------------------------------------------------------------------------


class TestEndpointSelection:

    @pytest.mark.asyncio
    @respx.mock
    async def test_post_gets_all_three_check_types(
        self, post_only_config: SentinelConfig
    ) -> None:
        """POST endpoint gets oversized + malformed + 4 injection = 6 results."""
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(400)
        )
        with patch("sentinel.checks.input_handling.resolve_env_var", return_value="tok"):
            check = InputHandlingCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(post_only_config, client)

        check_ids = [r.check_id for r in results]
        assert "input.oversized_payload_resource" in check_ids
        assert "input.malformed_content_type_resource" in check_ids
        assert sum(1 for c in check_ids if "injection" in c) == 4
        assert len(results) == 6

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_gets_only_injection(self, public_get_config: SentinelConfig) -> None:
        """GET endpoint only gets 4 injection probe results."""
        respx.get(url__startswith="https://api.test.com/health").mock(
            return_value=httpx.Response(400)
        )
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(public_get_config, client)

        assert len(results) == 4
        assert all("injection" in r.check_id for r in results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_public_endpoint_no_auth_header(
        self, public_get_config: SentinelConfig
    ) -> None:
        """Public endpoint requests omit Authorization header."""
        received_headers: list[dict] = []

        def capture(request: httpx.Request) -> httpx.Response:
            received_headers.append(dict(request.headers))
            return httpx.Response(400)

        respx.get(url__startswith="https://api.test.com/health").mock(side_effect=capture)
        check = InputHandlingCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            await check.run(public_get_config, client)

        assert all("authorization" not in h for h in received_headers)


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_full_run_result_count(self, input_config: SentinelConfig) -> None:
        """POST (6 results) + GET (4 results) = 10 total."""
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(400)
        )
        respx.get(url__startswith="https://api.test.com/resource/1").mock(
            return_value=httpx.Response(400)
        )
        with patch("sentinel.checks.input_handling.resolve_env_var", return_value="tok"):
            check = InputHandlingCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(input_config, client)

        assert len(results) == 10

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_ids_unique(self, input_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            return_value=httpx.Response(400)
        )
        respx.get(url__startswith="https://api.test.com/resource/1").mock(
            return_value=httpx.Response(400)
        )
        with patch("sentinel.checks.input_handling.resolve_env_var", return_value="tok"):
            check = InputHandlingCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(input_config, client)

        check_ids = [r.check_id for r in results]
        assert len(check_ids) == len(set(check_ids))

    @pytest.mark.asyncio
    @respx.mock
    async def test_connection_error(self, post_only_config: SentinelConfig) -> None:
        respx.post(url__startswith="https://api.test.com/resource").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with patch("sentinel.checks.input_handling.resolve_env_var", return_value="tok"):
            check = InputHandlingCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(post_only_config, client)

        # All results should be CRITICAL connection errors
        assert all(r.severity == Severity.CRITICAL for r in results)
        assert all(not r.passed for r in results)
