"""Safe environment variable resolution.

Secrets are never stored in config files. The YAML references env var names,
and this module resolves them to values at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path


class EnvVarError(Exception):
    """Raised when a required environment variable is not set."""

    def __init__(self, var_name: str, description: str = "") -> None:
        self.var_name = var_name
        self.description = description
        msg = f"Environment variable '{var_name}' is not set."
        if description:
            msg += f" This is required for: {description}."
        super().__init__(msg)


def resolve_env_var(
    var_name: str,
    *,
    required: bool = True,
    description: str = "",
) -> str | None:
    """Resolve an environment variable by name.

    Args:
        var_name: The environment variable name to look up.
        required: If True, raises EnvVarError when the variable is not set.
        description: Human-readable purpose, used in error messages.

    Returns:
        The variable's value, or None if not set and not required.

    Raises:
        EnvVarError: If the variable is not set and required is True.
    """
    value = os.environ.get(var_name)
    if value is None and required:
        raise EnvVarError(var_name, description)
    return value


def load_dotenv_file(path: Path = Path(".env")) -> None:
    """Load environment variables from a .env file if it exists.

    Uses python-dotenv. Does not override existing environment variables.
    Silently skips if the file does not exist.
    """
    if not path.exists():
        return
    from dotenv import load_dotenv

    load_dotenv(path, override=False)
