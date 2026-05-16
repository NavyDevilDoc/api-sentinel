"""`sentinel ui` launcher — port selection, browser open, uvicorn boot.

The launch flow:
  1. Try the requested port; fall back to an OS-assigned free port if busy.
  2. Print the URL prominently — this is the canonical instruction.
  3. Schedule a best-effort browser open from a daemon thread, 1s after launch.
  4. Boot uvicorn (blocking) until Ctrl+C.
"""

from __future__ import annotations

import socket
import threading
import webbrowser
from contextlib import closing

from rich.console import Console

from sentinel.ui.server import create_app


def _is_port_free(host: str, port: int) -> bool:
    """Return True if (host, port) can be bound right now.

    There's an unavoidable race between the probe and the actual uvicorn
    bind, but in practice the window is sub-millisecond on a local machine.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, requested_port: int) -> tuple[int, bool]:
    """Try the requested port; pick a free one from the OS if it's busy.

    Returns:
        (port_to_use, fallback_used)
    """
    if _is_port_free(host, requested_port):
        return requested_port, False

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        free_port = s.getsockname()[1]
    return free_port, True


def _open_browser_best_effort(url: str) -> None:
    """Open the default browser. Failures are silently swallowed because the
    URL has already been printed to the terminal — the user has a fallback."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def launch(
    *,
    host: str,
    port: int,
    no_browser: bool,
    console: Console,
) -> int:
    """Boot the FastAPI app via uvicorn. Returns an exit code."""
    import uvicorn

    actual_port, fallback_used = _pick_port(host, port)

    if fallback_used:
        console.print(
            f"[yellow]Port {port} is in use. "
            f"Listening on port {actual_port} instead.[/yellow]"
        )

    url = f"http://{host}:{actual_port}"

    console.print(
        f"\n[bold cyan]API Sentinel UI[/bold cyan]\n"
        f"  URL:     [link]{url}[/link]\n"
        f"  Press Ctrl+C to stop.\n"
    )

    if not no_browser:
        # Wait 1s to give uvicorn time to bind before opening the browser.
        # If we open too early the browser shows connection-refused, but the
        # URL is in the terminal so the user can refresh.
        timer = threading.Timer(1.0, _open_browser_best_effort, args=[url])
        timer.daemon = True
        timer.start()

    app = create_app()

    try:
        uvicorn.run(
            app,
            host=host,
            port=actual_port,
            log_level="warning",
            access_log=False,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")

    return 0
