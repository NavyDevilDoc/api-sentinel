"""Shared httpx client factory.

All HTTP calls go through clients created here, ensuring consistent
timeout, HTTP/2, and redirect behavior across every check module.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx


def parse_hostname_port(url: str) -> tuple[str, int]:
    """Extract hostname and port from a URL.

    Defaults to 443 for https, 80 for http.

    Args:
        url: A full URL string (e.g. "https://api.example.com:8443").

    Returns:
        (hostname, port) tuple.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return hostname, port


def create_client(
    base_url: str,
    timeout_seconds: float = 10.0,
    auth_token: str | None = None,
) -> httpx.AsyncClient:
    """Create a configured async HTTP client.

    Args:
        base_url: The API base URL (e.g. "https://api.example.com").
        timeout_seconds: Timeout for all requests. Explicit per hard constraint.
        auth_token: Optional bearer token injected as default Authorization header.

    Returns:
        An httpx.AsyncClient configured for security checking.
        Caller is responsible for closing the client (use as async context manager).
    """
    headers: dict[str, str] = {}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"

    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        headers=headers,
        http2=True,
        follow_redirects=False,
    )
