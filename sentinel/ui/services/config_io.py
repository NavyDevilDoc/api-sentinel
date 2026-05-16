"""Config I/O wrapper for the UI layer.

Bridges the UI to the existing pydantic config loader. Adds:
  - Path resolution with traversal protection (relative paths must stay
    under cwd; absolute paths are accepted because the user explicitly typed them).
  - Non-throwing load that returns a structured ConfigLoadResult instead of
    raising — the UI renders error states inline.
  - EnvVarStatus: a display-only dataclass that captures whether a configured
    env var is set, without capturing its value.

The "never capture the value" discipline is enforced by code structure: there
is no field, attribute, or repr that could ever hold the resolved secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from sentinel.config import SentinelConfig, load_config


@dataclass
class EnvVarStatus:
    """Display state for a configured env var. The value is intentionally
    never read into this struct — only presence is recorded."""

    name: str
    is_set: bool

    @property
    def display(self) -> str:
        return f"{self.name} (not set)" if not self.is_set else self.name


@dataclass
class ConfigLoadResult:
    """Outcome of a load attempt — success or a descriptive failure message.

    The UI calls this instead of letting pydantic exceptions bubble up, so
    error states render inside the page rather than as 500s.
    """

    config: SentinelConfig | None
    path: Path
    error: str | None = None

    @property
    def loaded(self) -> bool:
        return self.config is not None


def resolve_config_path(raw: str | None, cwd: Path | None = None) -> Path:
    """Resolve a config path with traversal protection.

    Rules:
      - None or "" → cwd / "sentinel_config.yaml" (matches the CLI default).
      - Absolute path → accepted as-is (user explicitly typed it).
      - Relative path → resolved against cwd; must stay under cwd.

    Raises:
        ValueError: if a relative path resolves outside cwd.
    """
    cwd = cwd or Path.cwd()

    if raw is None or raw == "":
        return cwd / "sentinel_config.yaml"

    path = Path(raw)
    if path.is_absolute():
        return path

    candidate = (cwd / path).resolve()
    cwd_resolved = cwd.resolve()

    try:
        candidate.relative_to(cwd_resolved)
    except ValueError as e:
        raise ValueError(
            f"Relative config path {raw!r} resolves outside the working "
            f"directory. Use an absolute path if intentional."
        ) from e

    return candidate


def load_config_for_viewer(path: Path) -> ConfigLoadResult:
    """Attempt to load + validate a config. Errors are returned, not raised."""
    if not path.exists():
        return ConfigLoadResult(
            config=None,
            path=path,
            error=f"No config file found at {path}",
        )

    try:
        config = load_config(path)
    except FileNotFoundError as e:
        return ConfigLoadResult(config=None, path=path, error=str(e))
    except ValidationError as e:
        return ConfigLoadResult(
            config=None, path=path, error=f"Validation error: {e}"
        )
    except ValueError as e:
        return ConfigLoadResult(config=None, path=path, error=str(e))
    except Exception as e:
        # YAML parse errors, permission errors, etc.
        return ConfigLoadResult(
            config=None, path=path, error=f"Failed to load config: {e}"
        )

    return ConfigLoadResult(config=config, path=path)


def env_var_status(name: str | None) -> EnvVarStatus | None:
    """Return display status for an env var name. None propagates as None."""
    if name is None:
        return None
    return EnvVarStatus(name=name, is_set=bool(os.environ.get(name)))
