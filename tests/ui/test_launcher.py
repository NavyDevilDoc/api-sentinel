"""Phase UI-2 — tests for the launcher helpers (port picker, browser handling).

The `launch()` function itself is not unit-tested here — it calls
`uvicorn.run()` which is blocking. Integration testing of the running server
happens via `test_routes_pages.py` against a TestClient-backed app.
"""

from __future__ import annotations

import socket
from contextlib import closing
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")

from sentinel.ui.launcher import (  # noqa: E402
    _is_port_free,
    _open_browser_best_effort,
    _pick_port,
)


def _free_port() -> int:
    """Return a port that was free at the moment of this call."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestIsPortFree:
    def test_known_free_port_returns_true(self) -> None:
        assert _is_port_free("127.0.0.1", _free_port()) is True

    def test_occupied_port_returns_false(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        try:
            occupied = sock.getsockname()[1]
            assert _is_port_free("127.0.0.1", occupied) is False
        finally:
            sock.close()


class TestPickPort:
    def test_returns_requested_port_when_free(self) -> None:
        free = _free_port()
        port, fallback = _pick_port("127.0.0.1", free)
        assert port == free
        assert fallback is False

    def test_falls_back_when_requested_port_in_use(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        try:
            occupied = sock.getsockname()[1]
            port, fallback = _pick_port("127.0.0.1", occupied)
            assert port != occupied
            assert fallback is True
            assert port > 0
        finally:
            sock.close()


class TestOpenBrowserBestEffort:
    def test_calls_webbrowser_open(self) -> None:
        with patch("sentinel.ui.launcher.webbrowser.open") as mock_open:
            _open_browser_best_effort("http://127.0.0.1:8765")
            mock_open.assert_called_once_with("http://127.0.0.1:8765")

    def test_swallows_exceptions(self) -> None:
        with patch(
            "sentinel.ui.launcher.webbrowser.open",
            side_effect=RuntimeError("no display"),
        ):
            # Must not raise — caller relies on this being best-effort.
            _open_browser_best_effort("http://127.0.0.1:8765")
