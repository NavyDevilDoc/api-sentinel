"""Tests for the check orchestrator's defensive error handling.

A single check raising an unhandled exception must not crash the entire
scan. The runner catches per-category exceptions and surfaces them as
CRITICAL findings so other check categories continue running.
"""

from __future__ import annotations

import pytest

from sentinel.checks.base import BaseCheck, CheckResult, Severity
from sentinel.config import (
    AuthConfig,
    MetaConfig,
    SentinelConfig,
)
from sentinel.runner import CHECK_REGISTRY, run_checks


def _make_minimal_config() -> SentinelConfig:
    """A config that points at an unreachable host. Combined with monkey-
    patched fake checks, no real network calls are made during the test."""
    return SentinelConfig(
        meta=MetaConfig(project="test", base_url="https://example.invalid"),
        auth=AuthConfig(token_primary="SENTINEL_TEST_TOK"),
        endpoints=[],
        # Disable authorization so its model_validator doesn't require
        # token_secondary.
        checks={"authorization": {"enabled": False}},  # type: ignore[arg-type]
    )


class _FailingTransportCheck(BaseCheck):
    """Fake check that always raises — stand-in for a real check hitting
    an unrecoverable protocol/environment error."""

    check_category = "transport"

    async def run(self, config, client):
        raise RuntimeError("simulated protocol error")


class _PassingHeadersCheck(BaseCheck):
    """Fake check that produces a known-id finding so we can assert it
    ran even when another check crashed."""

    check_category = "headers"

    async def run(self, config, client):
        return [CheckResult(
            check_id="headers.test_marker",
            name="Test marker",
            severity=Severity.PASS,
            passed=True,
            detail="reached marker",
            expected="ok",
            recommendation="",
        )]


@pytest.mark.asyncio
async def test_unhandled_check_exception_becomes_critical_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: one check raising must not abort the entire scan; it
    should surface as a CRITICAL finding scoped to that category."""
    monkeypatch.setitem(CHECK_REGISTRY, "transport", _FailingTransportCheck)

    config = _make_minimal_config()
    result = await run_checks(config, selected_checks=["transport"])

    unhandled = [
        r for r in result.results
        if r.check_id == "transport.unhandled_exception"
    ]
    assert len(unhandled) == 1
    finding = unhandled[0]
    assert finding.severity == Severity.CRITICAL
    assert finding.passed is False
    # The original exception's type and message must be surfaced
    assert "RuntimeError" in finding.detail
    assert "simulated protocol error" in finding.detail
    # Recommendation should mention the kinds of mitigations available
    assert "max_payload_kb" in finding.recommendation


@pytest.mark.asyncio
async def test_failing_check_does_not_prevent_other_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the per-category try/except: when transport
    crashes, headers still gets to produce its findings."""
    monkeypatch.setitem(CHECK_REGISTRY, "transport", _FailingTransportCheck)
    monkeypatch.setitem(CHECK_REGISTRY, "headers", _PassingHeadersCheck)

    config = _make_minimal_config()
    result = await run_checks(
        config, selected_checks=["transport", "headers"]
    )

    # Transport produced its critical finding (proving it was attempted)
    assert any(
        r.check_id == "transport.unhandled_exception"
        for r in result.results
    )
    # Headers produced its marker (proving it ran after transport crashed)
    assert any(
        r.check_id == "headers.test_marker" for r in result.results
    )


@pytest.mark.asyncio
async def test_run_result_returned_even_when_check_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan must return a RunResult — not propagate the exception.
    Without this guarantee, the UI's scan-status state machine would
    flip to 'error' on every recoverable check failure instead of
    showing the partial findings."""
    monkeypatch.setitem(CHECK_REGISTRY, "transport", _FailingTransportCheck)

    config = _make_minimal_config()
    # Should NOT raise
    result = await run_checks(config, selected_checks=["transport"])

    assert result is not None
    assert result.results  # at least the unhandled_exception finding
    assert result.duration_ms > 0
