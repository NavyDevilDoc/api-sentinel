"""Security tests for header checks."""

from __future__ import annotations

import httpx
import pytest
import respx

from sentinel.checks.base import Severity
from sentinel.checks.headers import HeadersCheck
from sentinel.config import SentinelConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def headers_config() -> SentinelConfig:
    """Config with header checks enabled."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
        "endpoints": [],
        "checks": {
            "headers": {
                "enabled": True,
                "required": [
                    "Strict-Transport-Security",
                    "X-Content-Type-Options",
                    "X-Frame-Options",
                ],
                "forbidden_leakage": [
                    "X-Powered-By",
                    "Server",
                ],
            },
        },
    })


@pytest.fixture
def empty_lists_config() -> SentinelConfig:
    """Config with empty required and forbidden lists."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
        "endpoints": [],
        "checks": {
            "headers": {
                "enabled": True,
                "required": [],
                "forbidden_leakage": [],
            },
        },
    })


# ---------------------------------------------------------------------------
# Required Headers
# ---------------------------------------------------------------------------


class TestRequiredHeaders:

    @pytest.mark.asyncio
    @respx.mock
    async def test_required_header_present(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "Strict-Transport-Security": "max-age=31536000",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        required_results = [r for r in results if r.check_id.startswith("headers.required_")]
        assert len(required_results) == 3
        assert all(r.passed for r in required_results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_required_header_missing(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={})
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        required_results = [r for r in results if r.check_id.startswith("headers.required_")]
        assert len(required_results) == 3
        assert all(not r.passed for r in required_results)
        assert all(r.severity == Severity.CRITICAL for r in required_results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_mixed_required_headers(self, headers_config: SentinelConfig) -> None:
        """Some headers present, some missing."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "Strict-Transport-Security": "max-age=31536000",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        required_results = [r for r in results if r.check_id.startswith("headers.required_")]
        passed = [r for r in required_results if r.passed]
        failed = [r for r in required_results if not r.passed]
        assert len(passed) == 1
        assert len(failed) == 2


# ---------------------------------------------------------------------------
# Forbidden (Leakage) Headers
# ---------------------------------------------------------------------------


class TestForbiddenHeaders:

    @pytest.mark.asyncio
    @respx.mock
    async def test_forbidden_header_absent(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={})
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        leakage_results = [r for r in results if r.check_id.startswith("headers.leakage_")]
        assert len(leakage_results) == 2
        assert all(r.passed for r in leakage_results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_forbidden_header_present(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "X-Powered-By": "Express 4.18.2",
                "Server": "nginx/1.21.3",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        leakage_results = [r for r in results if r.check_id.startswith("headers.leakage_")]
        assert len(leakage_results) == 2
        assert all(not r.passed for r in leakage_results)
        assert all(r.severity == Severity.CRITICAL for r in leakage_results)

    @pytest.mark.asyncio
    @respx.mock
    async def test_forbidden_header_value_captured(self, headers_config: SentinelConfig) -> None:
        """The leaked header value appears in the detail string."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "X-Powered-By": "Express 4.18.2",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        xpb = next(r for r in results if "x_powered_by" in r.check_id)
        assert "Express 4.18.2" in xpb.detail


# ---------------------------------------------------------------------------
# CORS Wildcard
# ---------------------------------------------------------------------------


class TestCorsWildcard:

    @pytest.mark.asyncio
    @respx.mock
    async def test_cors_wildcard_warning(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "Access-Control-Allow-Origin": "*",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        cors = next(r for r in results if r.check_id == "headers.cors_wildcard")
        assert cors.passed is False
        assert cors.severity == Severity.WARNING

    @pytest.mark.asyncio
    @respx.mock
    async def test_cors_specific_origin_pass(self, headers_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "Access-Control-Allow-Origin": "https://app.example.com",
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        cors = next(r for r in results if r.check_id == "headers.cors_wildcard")
        assert cors.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_cors_absent_no_result(self, headers_config: SentinelConfig) -> None:
        """When no CORS header exists, no CORS result is produced."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={})
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        cors_results = [r for r in results if "cors" in r.check_id]
        assert len(cors_results) == 0


# ---------------------------------------------------------------------------
# Empty Config Lists
# ---------------------------------------------------------------------------


class TestEmptyLists:

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_required_and_forbidden(self, empty_lists_config: SentinelConfig) -> None:
        """Empty required/forbidden lists produce no header results."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={})
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(empty_lists_config, client)

        assert len(results) == 0


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullHeadersRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_full_run(self, headers_config: SentinelConfig) -> None:
        """Full run with mixed pass/fail headers and CORS wildcard."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={
                "Strict-Transport-Security": "max-age=31536000",
                "X-Content-Type-Options": "nosniff",
                # X-Frame-Options missing (fail)
                "X-Powered-By": "Express 4.18.2",  # leakage (fail)
                # Server absent (pass)
                "Access-Control-Allow-Origin": "*",  # CORS wildcard (warning)
            })
        )
        check = HeadersCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            results = await check.run(headers_config, client)

        # 3 required + 2 forbidden + 1 CORS = 6
        assert len(results) == 6

        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        assert len(passed) == 3  # 2 required present + 1 forbidden absent
        assert len(failed) == 3  # 1 required missing + 1 forbidden present + 1 CORS wildcard

        # Verify severities
        cors = next(r for r in results if r.check_id == "headers.cors_wildcard")
        assert cors.severity == Severity.WARNING

        xpb = next(r for r in results if "x_powered_by" in r.check_id)
        assert xpb.severity == Severity.CRITICAL
