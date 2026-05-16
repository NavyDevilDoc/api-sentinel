"""Phase UI-6 — service-layer tests for the in-memory scan runner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from sentinel.checks.base import CheckResult, Severity  # noqa: E402
from sentinel.config import (  # noqa: E402
    AuthConfig,
    EndpointConfig,
    MetaConfig,
    SentinelConfig,
)
from sentinel.runner import RunResult  # noqa: E402
from sentinel.ui.services import scan_runner as scan_runner_mod  # noqa: E402
from sentinel.ui.services.scan_runner import ScanRunner  # noqa: E402


def _make_config() -> SentinelConfig:
    return SentinelConfig(
        meta=MetaConfig(project="test-proj", base_url="https://example.com"),
        auth=AuthConfig(token_primary="SENTINEL_TEST_TOK"),
        endpoints=[],
        checks={"authorization": {"enabled": False}},  # type: ignore[arg-type]
    )


def _make_run_result() -> RunResult:
    return RunResult(
        results=[
            CheckResult(
                check_id="transport.https",
                name="HTTPS enforced",
                severity=Severity.PASS,
                passed=True,
                detail="ok",
                expected="HTTPS",
                recommendation="",
            ),
        ],
        timestamp=datetime.now(timezone.utc),
        duration_ms=12.3,
        checks_run=["transport"],
        checks_skipped=[],
    )


@pytest.fixture
def runner() -> ScanRunner:
    """A fresh runner per test — avoids the module-level singleton's state."""
    return ScanRunner()


@pytest.mark.asyncio
async def test_start_returns_uuid_and_creates_running_state(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set up a fake run_checks that never returns so we can check the
    'running' state without a race."""
    sentinel_run_called = asyncio.Event()
    stop_runner = asyncio.Event()

    async def slow_run_checks(config, selected=None):
        sentinel_run_called.set()
        await stop_runner.wait()
        return _make_run_result()

    monkeypatch.setattr(scan_runner_mod, "run_checks", slow_run_checks)

    scan_id = await runner.start(_make_config(), "/tmp/test-config.yaml")
    assert len(scan_id) == 36  # uuid4 hyphenated length

    await sentinel_run_called.wait()  # background task is in flight
    state = await runner.get(scan_id)
    assert state is not None
    assert state.status == "running"
    assert state.target == "https://example.com"
    assert state.project == "test-proj"
    assert state.run_result is None

    # Let the task finish so it doesn't dangle
    stop_runner.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_completed_scan_transitions_to_complete(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fast_run_checks(config, selected=None):
        return _make_run_result()

    monkeypatch.setattr(scan_runner_mod, "run_checks", fast_run_checks)

    scan_id = await runner.start(_make_config(), "/tmp/test-config.yaml")

    # Poll briefly for completion
    for _ in range(50):
        state = await runner.get(scan_id)
        if state and state.is_done:
            break
        await asyncio.sleep(0.02)

    state = await runner.get(scan_id)
    assert state is not None
    assert state.status == "complete"
    assert state.run_result is not None
    assert state.completed_at is not None
    assert state.error is None


@pytest.mark.asyncio
async def test_failing_scan_transitions_to_error(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_run_checks(config, selected=None):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(scan_runner_mod, "run_checks", failing_run_checks)

    scan_id = await runner.start(_make_config(), "/tmp/test-config.yaml")

    for _ in range(50):
        state = await runner.get(scan_id)
        if state and state.is_done:
            break
        await asyncio.sleep(0.02)

    state = await runner.get(scan_id)
    assert state is not None
    assert state.status == "error"
    assert state.run_result is None
    assert "simulated network failure" in state.error
    assert "RuntimeError" in state.error


@pytest.mark.asyncio
async def test_get_unknown_id_returns_none(runner: ScanRunner) -> None:
    state = await runner.get("00000000-0000-0000-0000-000000000000")
    assert state is None


@pytest.mark.asyncio
async def test_list_recent_orders_by_started_at_desc(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fast_run_checks(config, selected=None):
        return _make_run_result()

    monkeypatch.setattr(scan_runner_mod, "run_checks", fast_run_checks)

    # asyncio.sleep precision can be coarse on Windows; use a comfortable
    # margin to guarantee distinct started_at timestamps for the sort.
    id1 = await runner.start(_make_config(), "/tmp/p1.yaml")
    await asyncio.sleep(0.05)
    id2 = await runner.start(_make_config(), "/tmp/p2.yaml")
    await asyncio.sleep(0.05)
    id3 = await runner.start(_make_config(), "/tmp/p3.yaml")

    recent = await runner.list_recent()
    assert [s.id for s in recent[:3]] == [id3, id2, id1]


@pytest.mark.asyncio
async def test_list_recent_respects_limit(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fast_run_checks(config, selected=None):
        return _make_run_result()

    monkeypatch.setattr(scan_runner_mod, "run_checks", fast_run_checks)

    for _ in range(5):
        await runner.start(_make_config(), "/tmp/test-config.yaml")
        await asyncio.sleep(0.001)

    recent = await runner.list_recent(limit=3)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_clear_drops_all_state(
    runner: ScanRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fast_run_checks(config, selected=None):
        return _make_run_result()

    monkeypatch.setattr(scan_runner_mod, "run_checks", fast_run_checks)

    await runner.start(_make_config(), "/tmp/test.yaml")
    await runner.start(_make_config(), "/tmp/test.yaml")
    await runner.clear()

    assert await runner.list_recent() == []


def test_scan_state_duration_ms_live_while_running() -> None:
    """The duration property should update from now() while still running."""
    from sentinel.ui.services.scan_runner import ScanState
    from datetime import timedelta

    started = datetime.now(timezone.utc) - timedelta(milliseconds=500)
    state = ScanState(
        id="x",
        target="t",
        project="p",
        config=_make_config(),
        config_path="/tmp/x.yaml",
        status="running",
        started_at=started,
    )
    assert state.duration_ms is not None
    assert 400 <= state.duration_ms <= 2000


def test_scan_state_duration_ms_frozen_once_complete() -> None:
    from sentinel.ui.services.scan_runner import ScanState
    from datetime import timedelta

    started = datetime.now(timezone.utc) - timedelta(seconds=1)
    completed = started + timedelta(milliseconds=200)
    state = ScanState(
        id="x",
        target="t",
        project="p",
        config=_make_config(),
        config_path="/tmp/x.yaml",
        status="complete",
        started_at=started,
        completed_at=completed,
    )
    # Should reflect completed - started (200ms), not now - started (1000+ ms)
    assert 195 <= state.duration_ms <= 210
