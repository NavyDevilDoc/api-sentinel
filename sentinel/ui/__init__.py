"""API Sentinel UI — optional FastAPI + HTMX + Jinja web frontend.

This package is only importable when the [ui] extras are installed:
    pip install 'api-sentinel[ui]'

The UI is an alternate front-end over the same runner/reporter primitives
used by the CLI. It binds to localhost only and never accepts secret values
as input — only environment variable names.
"""

from __future__ import annotations

try:
    import fastapi  # noqa: F401
except ImportError as e:
    raise ImportError(
        "API Sentinel UI extras are not installed. "
        "Run: pip install 'api-sentinel[ui]'"
    ) from e
