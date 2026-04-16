"""Transport security checks.

Validates HTTPS enforcement, HTTP-to-HTTPS redirect behavior, TLS version,
certificate expiry, and HSTS header presence. These are stateless checks
that do not require authentication.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from sentinel.checks.base import BaseCheck, CheckResult, Severity, make_result
from sentinel.utils.http_client import parse_hostname_port

if TYPE_CHECKING:
    from sentinel.config import SentinelConfig


def _tls_info(hostname: str, port: int, timeout: float) -> tuple[str, dict]:
    """Open a single SSL socket and return TLS version and peer certificate.

    This is a blocking call -- intended to be run via run_in_executor.

    Args:
        hostname: Target hostname.
        port: Target port (typically 443).
        timeout: Socket timeout in seconds.

    Returns:
        (tls_version_str, cert_dict) -- e.g. ("TLSv1.3", {...}).

    Raises:
        ssl.SSLError: TLS handshake failure.
        socket.error: Connection failure.
        OSError: Other socket errors.
    """
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            version = ssock.version() or "unknown"
            cert = ssock.getpeercert() or {}
            return version, cert


def _parse_tls_version(version_str: str) -> float:
    """Parse a TLS version string like 'TLSv1.3' into a float like 1.3."""
    cleaned = version_str.replace("TLSv", "").replace("SSLv", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_cert_expiry(cert_dict: dict) -> datetime | None:
    """Extract the notAfter date from a peer certificate dict.

    Returns a timezone-aware datetime, or None if parsing fails.
    """
    not_after = cert_dict.get("notAfter")
    if not not_after:
        return None
    try:
        # Format: 'Apr 15 12:00:00 2027 GMT'
        ts = ssl.cert_time_to_seconds(not_after)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


class TransportCheck(BaseCheck):
    """Transport-layer security checks."""

    check_category = "transport"

    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []

        # 1. HTTPS enforced (pure string check)
        results.append(self._check_https_enforced(config))

        # 2. HTTP -> HTTPS redirect (separate HTTP client)
        if config.checks.transport.require_https_redirect:
            results.append(await self._check_https_redirect(config))

        # 3 & 4. TLS version + certificate expiry (shared SSL socket)
        hostname, port = parse_hostname_port(config.meta.base_url)
        loop = asyncio.get_event_loop()
        try:
            tls_version, cert_dict = await loop.run_in_executor(
                None, _tls_info, hostname, port, config.meta.timeout_seconds
            )
            results.append(self._check_tls_version(config, tls_version))
            results.append(self._check_cert_expiry(config, cert_dict))
        except (ssl.SSLError, socket.error, OSError) as e:
            results.append(CheckResult(
                check_id="transport.tls_version",
                name="TLS version check",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"TLS connection failed: {e}",
                expected=f"TLS >= {config.checks.transport.min_tls_version}",
                recommendation="Verify the server supports TLS and is reachable.",
            ))
            results.append(CheckResult(
                check_id="transport.cert_expiry",
                name="Certificate expiry check",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Could not retrieve certificate: {e}",
                expected=f"Certificate valid for > {config.checks.transport.check_cert_expiry_days} days",
                recommendation="Verify the server supports TLS and is reachable.",
            ))

        # 5. HSTS header
        results.append(await self._check_hsts(config, client))

        return results

    def _check_https_enforced(self, config: SentinelConfig) -> CheckResult:
        """Verify the base URL uses HTTPS."""
        parsed = urlparse(config.meta.base_url)
        is_https = parsed.scheme == "https"
        return make_result(
            check_id="transport.https_enforced",
            name="HTTPS enforced",
            passed=is_https,
            detail=f"Base URL scheme: {parsed.scheme}://",
            expected="https:// scheme",
            recommendation="Configure your API with TLS. Update base_url to use https://.",
        )

    async def _check_https_redirect(self, config: SentinelConfig) -> CheckResult:
        """Check that HTTP requests redirect to HTTPS."""
        http_url = config.meta.base_url.replace("https://", "http://", 1)
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(config.meta.timeout_seconds),
                follow_redirects=False,
            ) as http_client:
                response = await http_client.get(http_url)
                latency = (time.monotonic() - start) * 1000

                location = response.headers.get("location", "")
                is_redirect = response.status_code in (301, 302, 307, 308)
                redirects_to_https = location.startswith("https://")

                if is_redirect and redirects_to_https:
                    return make_result(
                        check_id="transport.https_redirect",
                        name="HTTP redirects to HTTPS",
                        passed=True,
                        detail=f"HTTP {response.status_code} -> {location}",
                        expected="301/302 redirect to HTTPS",
                        response_code=response.status_code,
                        latency_ms=round(latency, 2),
                    )
                elif is_redirect and not redirects_to_https:
                    return make_result(
                        check_id="transport.https_redirect",
                        name="HTTP redirects to HTTPS",
                        passed=False,
                        detail=f"Redirects to non-HTTPS location: {location}",
                        expected="301/302 redirect to HTTPS",
                        recommendation="Configure HTTP redirect target to use https://.",
                        response_code=response.status_code,
                        latency_ms=round(latency, 2),
                    )
                else:
                    return make_result(
                        check_id="transport.https_redirect",
                        name="HTTP redirects to HTTPS",
                        passed=False,
                        detail=f"HTTP request returned {response.status_code} with no redirect",
                        expected="301/302 redirect to HTTPS",
                        recommendation="Configure your server to redirect HTTP to HTTPS.",
                        response_code=response.status_code,
                        latency_ms=round(latency, 2),
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError):
            return CheckResult(
                check_id="transport.https_redirect",
                name="HTTP redirects to HTTPS",
                severity=Severity.WARNING,
                passed=False,
                detail="Port 80 is unreachable (connection refused or timed out)",
                expected="301/302 redirect to HTTPS",
                recommendation="Port 80 appears closed. This may be intentional if HTTPS-only.",
            )

    def _check_tls_version(
        self, config: SentinelConfig, tls_version: str
    ) -> CheckResult:
        """Verify the negotiated TLS version meets the minimum requirement."""
        negotiated = _parse_tls_version(tls_version)
        required = float(config.checks.transport.min_tls_version)
        passed = negotiated >= required

        return make_result(
            check_id="transport.tls_version",
            name=f"TLS {tls_version} detected",
            passed=passed,
            detail=f"Negotiated: {tls_version}",
            expected=f"TLS >= {config.checks.transport.min_tls_version}",
            recommendation=f"Upgrade server TLS configuration to support TLS {config.checks.transport.min_tls_version} or higher.",
        )

    def _check_cert_expiry(
        self, config: SentinelConfig, cert_dict: dict
    ) -> CheckResult:
        """Check that the server certificate is not expiring soon."""
        threshold = config.checks.transport.check_cert_expiry_days
        expiry_dt = _parse_cert_expiry(cert_dict)

        if expiry_dt is None:
            return CheckResult(
                check_id="transport.cert_expiry",
                name="Certificate expiry check",
                severity=Severity.WARNING,
                passed=False,
                detail="Could not parse certificate expiry date",
                expected=f"Certificate valid for > {threshold} days",
                recommendation="Verify the server certificate is properly configured.",
            )

        now = datetime.now(timezone.utc)
        days_remaining = (expiry_dt - now).days

        if days_remaining < 0:
            return CheckResult(
                check_id="transport.cert_expiry",
                name="Certificate expiry check",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Certificate EXPIRED {abs(days_remaining)} days ago",
                expected=f"Certificate valid for > {threshold} days",
                recommendation="Renew the server certificate immediately.",
            )

        passed = days_remaining > threshold
        if passed:
            severity = Severity.PASS
        else:
            severity = Severity.CRITICAL

        return CheckResult(
            check_id="transport.cert_expiry",
            name=f"Certificate valid ({days_remaining} days remaining)",
            severity=severity,
            passed=passed,
            detail=f"Certificate expires in {days_remaining} days (threshold: {threshold})",
            expected=f"Certificate valid for > {threshold} days",
            recommendation=f"Renew the certificate before it expires in {days_remaining} days.",
        )

    async def _check_hsts(
        self, config: SentinelConfig, client: httpx.AsyncClient
    ) -> CheckResult:
        """Check for Strict-Transport-Security header."""
        start = time.monotonic()
        try:
            response = await client.get("/")
            latency = (time.monotonic() - start) * 1000

            hsts = response.headers.get("strict-transport-security")
            if hsts:
                return make_result(
                    check_id="transport.hsts",
                    name="HSTS header present",
                    passed=True,
                    detail=f"Strict-Transport-Security: {hsts}",
                    expected="Strict-Transport-Security header present",
                    response_code=response.status_code,
                    latency_ms=round(latency, 2),
                )
            else:
                return make_result(
                    check_id="transport.hsts",
                    name="HSTS header present",
                    passed=False,
                    detail="Strict-Transport-Security header is missing",
                    expected="Strict-Transport-Security header present",
                    recommendation="Add Strict-Transport-Security header with appropriate max-age.",
                    response_code=response.status_code,
                    latency_ms=round(latency, 2),
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            return CheckResult(
                check_id="transport.hsts",
                name="HSTS header present",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Could not connect to check HSTS: {e}",
                expected="Strict-Transport-Security header present",
                recommendation="Verify the server is reachable.",
            )
