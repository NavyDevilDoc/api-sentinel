"""Configuration loader and pydantic models.

Reads sentinel_config.yaml and validates it against the full schema.
All data shapes are pydantic models — no raw dicts passed between modules.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Check-specific config models
# ---------------------------------------------------------------------------


class TransportCheckConfig(BaseModel):
    enabled: bool = True
    require_https_redirect: bool = True
    min_tls_version: str = "1.2"
    check_cert_expiry_days: int = 30


class HeadersCheckConfig(BaseModel):
    enabled: bool = True
    required: list[str] = []
    forbidden_leakage: list[str] = []


class AuthCheckConfig(BaseModel):
    enabled: bool = True


class AuthorizationCheckConfig(BaseModel):
    enabled: bool = True


class RateLimitCheckConfig(BaseModel):
    enabled: bool = True
    request_burst: int = 20
    burst_window_seconds: int = 5


class InputHandlingCheckConfig(BaseModel):
    enabled: bool = True
    max_payload_kb: int = 10240


class ChecksConfig(BaseModel):
    transport: TransportCheckConfig = TransportCheckConfig()
    headers: HeadersCheckConfig = HeadersCheckConfig()
    auth: AuthCheckConfig = AuthCheckConfig()
    authorization: AuthorizationCheckConfig = AuthorizationCheckConfig()
    rate_limit: RateLimitCheckConfig = RateLimitCheckConfig()
    input_handling: InputHandlingCheckConfig = InputHandlingCheckConfig()


# ---------------------------------------------------------------------------
# Top-level config models
# ---------------------------------------------------------------------------


class MetaConfig(BaseModel):
    project: str
    base_url: str
    timeout_seconds: float = 10.0

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class AuthConfig(BaseModel):
    token_primary: str  # env var NAME, never the raw secret
    token_secondary: str | None = None


class EndpointConfig(BaseModel):
    path: str
    method: str = "GET"
    requires_auth: bool = True
    test_ids: list[int | str] = []
    owned_by: str | None = None
    rate_limit_sensitive: bool = False


class SentinelConfig(BaseModel):
    """Root configuration model for sentinel_config.yaml."""

    meta: MetaConfig
    auth: AuthConfig
    endpoints: list[EndpointConfig]
    checks: ChecksConfig = ChecksConfig()

    @model_validator(mode="after")
    def bola_requires_secondary_token(self) -> SentinelConfig:
        if self.checks.authorization.enabled and self.auth.token_secondary is None:
            raise ValueError(
                "Authorization (BOLA) checks require 'token_secondary' in auth config. "
                "Either set auth.token_secondary or disable checks.authorization."
            )
        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Path = Path("sentinel_config.yaml")) -> SentinelConfig:
    """Load and validate sentinel config from a YAML file.

    Args:
        path: Path to the YAML config file.

    Returns:
        A validated SentinelConfig instance.

    Raises:
        FileNotFoundError: Config file does not exist.
        yaml.YAMLError: Invalid YAML syntax.
        pydantic.ValidationError: Schema violation.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)

    if raw is None:
        raise ValueError(f"Config file is empty: {path}")

    return SentinelConfig.model_validate(raw)
