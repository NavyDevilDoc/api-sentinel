"""Environment variable listing service.

Lists env var **names** matching a prefix. Values are intentionally never
inspected — this service operates on `os.environ.keys()`, never
`os.environ.get(name)`. The structural choice IS the security control: there
is no code path here that could surface a value.
"""

from __future__ import annotations

import os

DEFAULT_PREFIX = "SENTINEL_"


def list_env_var_names(prefix: str = DEFAULT_PREFIX) -> list[str]:
    """Return env var names matching `prefix`, sorted alphabetically.

    An empty prefix falls back to DEFAULT_PREFIX rather than returning every
    env var in the process — a footgun guard for the URL surface. Callers
    that genuinely want "everything" can pass `""` and get SENTINEL_*,
    which is the safest superset.
    """
    if not prefix:
        prefix = DEFAULT_PREFIX
    return sorted(name for name in os.environ if name.startswith(prefix))
