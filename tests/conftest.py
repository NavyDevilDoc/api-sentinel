"""Shared pytest fixtures for API Sentinel tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sentinel.checks.base import CheckResult, Severity
from sentinel.config import SentinelConfig


@pytest.fixture
def sample_config_dict() -> dict:
    """A valid config as a raw dict, matching sentinel_config.yaml schema."""
    return {
        "meta": {
            "project": "test-api",
            "base_url": "https://api.test.com",
            "timeout_seconds": 5,
        },
        "auth": {
            "token_primary": "TEST_TOKEN_A",
            "token_secondary": "TEST_TOKEN_B",
        },
        "endpoints": [
            {
                "path": "/resource/{id}",
                "method": "GET",
                "requires_auth": True,
                "test_ids": [1, 2, 3],
                "owned_by": "token_primary",
            },
            {
                "path": "/health",
                "method": "GET",
                "requires_auth": False,
            },
        ],
        "checks": {
            "transport": {"enabled": True},
            "headers": {
                "enabled": True,
                "required": ["Strict-Transport-Security"],
                "forbidden_leakage": ["X-Powered-By"],
            },
            "auth": {"enabled": True},
            "authorization": {"enabled": True},
            "rate_limit": {"enabled": True, "request_burst": 10},
            "input_handling": {"enabled": True},
        },
    }


@pytest.fixture
def sample_config(sample_config_dict: dict) -> SentinelConfig:
    """A validated SentinelConfig instance."""
    return SentinelConfig.model_validate(sample_config_dict)


@pytest.fixture
def sample_check_results() -> list[CheckResult]:
    """A mix of passed, warning, and critical results for reporter testing."""
    return [
        CheckResult(
            check_id="transport.https_redirect",
            name="HTTPS enforced",
            severity=Severity.PASS,
            passed=True,
            detail="HTTP redirects to HTTPS",
            expected="301/302 redirect to HTTPS",
            recommendation="",
        ),
        CheckResult(
            check_id="transport.tls_version",
            name="TLS 1.3 detected",
            severity=Severity.PASS,
            passed=True,
            detail="TLS 1.3 negotiated",
            expected="TLS >= 1.2",
            recommendation="",
        ),
        CheckResult(
            check_id="headers.xpoweredby",
            name="X-Powered-By leaks framework",
            severity=Severity.CRITICAL,
            passed=False,
            detail="X-Powered-By header exposes: Express 4.18.2",
            expected="X-Powered-By header should not be present",
            recommendation="app.disable('x-powered-by') or use helmet.js",
            endpoint="/resource/{id}",
            response_code=200,
            latency_ms=45.2,
        ),
        CheckResult(
            check_id="headers.server",
            name="Server header exposes version",
            severity=Severity.WARNING,
            passed=False,
            detail="Server header: nginx/1.21.3",
            expected="Server header should not expose version info",
            recommendation="Configure server to omit or genericize the Server header",
            endpoint="/resource/{id}",
            response_code=200,
            latency_ms=45.2,
        ),
        CheckResult(
            check_id="headers.xcontent_type_options",
            name="X-Content-Type-Options present",
            severity=Severity.PASS,
            passed=True,
            detail="X-Content-Type-Options: nosniff",
            expected="X-Content-Type-Options: nosniff",
            recommendation="",
        ),
    ]


@pytest.fixture
def tmp_config_file(tmp_path: Path, sample_config_dict: dict) -> Path:
    """Write sample config to a temporary YAML file and return the path."""
    config_path = tmp_path / "sentinel_config.yaml"
    config_path.write_text(yaml.dump(sample_config_dict), encoding="utf-8")
    return config_path
