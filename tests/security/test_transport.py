"""Security tests for transport checks."""

from __future__ import annotations

import ssl
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx

from sentinel.checks.base import Severity
from sentinel.checks.transport import TransportCheck, _parse_tls_version
from sentinel.config import SentinelConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def transport_config() -> SentinelConfig:
    """Config with transport checks enabled, HTTPS base URL."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com", "timeout_seconds": 5},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
        "endpoints": [],
        "checks": {
            "transport": {
                "enabled": True,
                "require_https_redirect": True,
                "min_tls_version": "1.2",
                "check_cert_expiry_days": 30,
            },
        },
    })


@pytest.fixture
def http_config() -> SentinelConfig:
    """Config with an HTTP (not HTTPS) base URL."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "http://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
        "endpoints": [],
        "checks": {
            "transport": {"enabled": True, "require_https_redirect": False},
        },
    })


@pytest.fixture
def no_redirect_config() -> SentinelConfig:
    """Config with require_https_redirect disabled."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK2"},
        "endpoints": [],
        "checks": {
            "transport": {"enabled": True, "require_https_redirect": False},
        },
    })


def _make_cert_dict(days_from_now: int) -> dict:
    """Build a mock certificate dict with notAfter set to N days from now."""
    expiry = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    # Format: 'Apr 15 12:00:00 2027 GMT'
    not_after = expiry.strftime("%b %d %H:%M:%S %Y GMT")
    return {"notAfter": not_after}


# ---------------------------------------------------------------------------
# HTTPS Enforced
# ---------------------------------------------------------------------------


class TestHttpsEnforced:

    @pytest.mark.asyncio
    async def test_https_pass(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_https_enforced(transport_config)
        assert result.passed is True
        assert result.check_id == "transport.https_enforced"
        assert result.severity == Severity.PASS

    @pytest.mark.asyncio
    async def test_http_fail(self, http_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_https_enforced(http_config)
        assert result.passed is False
        assert result.severity == Severity.CRITICAL
        assert "http://" in result.detail


# ---------------------------------------------------------------------------
# HTTPS Redirect
# ---------------------------------------------------------------------------


class TestHttpsRedirect:

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_pass(self, transport_config: SentinelConfig) -> None:
        respx.get("http://api.test.com").mock(
            return_value=httpx.Response(301, headers={"Location": "https://api.test.com"})
        )
        check = TransportCheck()
        result = await check._check_https_redirect(transport_config)
        assert result.passed is True
        assert result.response_code == 301

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_fail_no_redirect(self, transport_config: SentinelConfig) -> None:
        respx.get("http://api.test.com").mock(
            return_value=httpx.Response(200)
        )
        check = TransportCheck()
        result = await check._check_https_redirect(transport_config)
        assert result.passed is False
        assert result.severity == Severity.CRITICAL
        assert "no redirect" in result.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_fail_non_https_location(self, transport_config: SentinelConfig) -> None:
        respx.get("http://api.test.com").mock(
            return_value=httpx.Response(301, headers={"Location": "http://api.test.com/other"})
        )
        check = TransportCheck()
        result = await check._check_https_redirect(transport_config)
        assert result.passed is False
        assert "non-HTTPS" in result.detail

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_connection_refused(self, transport_config: SentinelConfig) -> None:
        respx.get("http://api.test.com").mock(side_effect=httpx.ConnectError("Connection refused"))
        check = TransportCheck()
        result = await check._check_https_redirect(transport_config)
        assert result.passed is False
        assert result.severity == Severity.WARNING
        assert "unreachable" in result.detail.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_skipped_when_disabled(self, no_redirect_config: SentinelConfig) -> None:
        """When require_https_redirect is False, the redirect check is not in results."""
        # Mock TLS and HSTS so the full run works
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={"Strict-Transport-Security": "max-age=31536000"})
        )
        with patch("sentinel.checks.transport._tls_info", return_value=("TLSv1.3", _make_cert_dict(90))):
            check = TransportCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(no_redirect_config, client)

        check_ids = [r.check_id for r in results]
        assert "transport.https_redirect" not in check_ids


# ---------------------------------------------------------------------------
# TLS Version
# ---------------------------------------------------------------------------


class TestTlsVersion:

    def test_parse_tls_version(self) -> None:
        assert _parse_tls_version("TLSv1.3") == 1.3
        assert _parse_tls_version("TLSv1.2") == 1.2
        assert _parse_tls_version("SSLv3.0") == 3.0
        assert _parse_tls_version("unknown") == 0.0

    @pytest.mark.asyncio
    async def test_tls_13_pass(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_tls_version(transport_config, "TLSv1.3")
        assert result.passed is True
        assert "1.3" in result.name

    @pytest.mark.asyncio
    async def test_tls_11_fail(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_tls_version(transport_config, "TLSv1.1")
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_tls_exact_min_pass(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_tls_version(transport_config, "TLSv1.2")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Certificate Expiry
# ---------------------------------------------------------------------------


class TestCertExpiry:

    @pytest.mark.asyncio
    async def test_cert_expiry_pass(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_cert_expiry(transport_config, _make_cert_dict(90))
        assert result.passed is True
        assert "threshold: 30" in result.detail

    @pytest.mark.asyncio
    async def test_cert_expiry_fail(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_cert_expiry(transport_config, _make_cert_dict(15))
        assert result.passed is False
        assert result.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_cert_expired(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_cert_expiry(transport_config, _make_cert_dict(-10))
        assert result.passed is False
        assert "EXPIRED" in result.detail

    @pytest.mark.asyncio
    async def test_cert_unparseable(self, transport_config: SentinelConfig) -> None:
        check = TransportCheck()
        result = check._check_cert_expiry(transport_config, {})
        assert result.passed is False
        assert result.severity == Severity.WARNING


# ---------------------------------------------------------------------------
# TLS Connection Error
# ---------------------------------------------------------------------------


class TestTlsConnectionError:

    @pytest.mark.asyncio
    @respx.mock
    async def test_tls_error_produces_both_error_results(
        self, transport_config: SentinelConfig
    ) -> None:
        """An SSL error produces CRITICAL results for both TLS and cert checks."""
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200, headers={"Strict-Transport-Security": "max-age=31536000"})
        )
        respx.get("http://api.test.com").mock(
            return_value=httpx.Response(301, headers={"Location": "https://api.test.com"})
        )

        with patch(
            "sentinel.checks.transport._tls_info",
            side_effect=ssl.SSLError("handshake failure"),
        ):
            check = TransportCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(transport_config, client)

        tls_result = next(r for r in results if r.check_id == "transport.tls_version")
        cert_result = next(r for r in results if r.check_id == "transport.cert_expiry")

        assert tls_result.passed is False
        assert tls_result.severity == Severity.CRITICAL
        assert "TLS connection failed" in tls_result.detail

        assert cert_result.passed is False
        assert cert_result.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# HSTS
# ---------------------------------------------------------------------------


class TestHsts:

    @pytest.mark.asyncio
    @respx.mock
    async def test_hsts_present(self, transport_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(
                200, headers={"Strict-Transport-Security": "max-age=31536000"}
            )
        )
        check = TransportCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_hsts(transport_config, client)
        assert result.passed is True
        assert result.response_code == 200

    @pytest.mark.asyncio
    @respx.mock
    async def test_hsts_absent(self, transport_config: SentinelConfig) -> None:
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(200)
        )
        check = TransportCheck()
        async with httpx.AsyncClient(base_url="https://api.test.com") as client:
            result = await check._check_hsts(transport_config, client)
        assert result.passed is False
        assert result.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Full Integration
# ---------------------------------------------------------------------------


class TestFullTransportRun:

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_checks_run(self, transport_config: SentinelConfig) -> None:
        """All 5 transport checks run and produce results."""
        respx.get("http://api.test.com").mock(
            return_value=httpx.Response(301, headers={"Location": "https://api.test.com"})
        )
        respx.get("https://api.test.com/").mock(
            return_value=httpx.Response(
                200, headers={"Strict-Transport-Security": "max-age=31536000"}
            )
        )

        with patch(
            "sentinel.checks.transport._tls_info",
            return_value=("TLSv1.3", _make_cert_dict(90)),
        ):
            check = TransportCheck()
            async with httpx.AsyncClient(base_url="https://api.test.com") as client:
                results = await check.run(transport_config, client)

        assert len(results) == 5
        check_ids = {r.check_id for r in results}
        assert check_ids == {
            "transport.https_enforced",
            "transport.https_redirect",
            "transport.tls_version",
            "transport.cert_expiry",
            "transport.hsts",
        }
        assert all(r.passed for r in results)
