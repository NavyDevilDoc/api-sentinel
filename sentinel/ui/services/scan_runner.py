"""Background scan execution and in-memory result registry.

Scans run as asyncio tasks created from POST /scans. Each scan gets a UUID
and a ScanState record in an in-memory dict keyed by id. The polling
endpoint reads from the same dict to render progress.

State lives only in memory: restarting the UI clears history. Persistence
(SQLite at .sentinel/runs.db) is a separate roadmap feature
("Historical Baseline Tracking" 🟡 in FUTURE_DEVELOPMENTS.md). v1 keeps it
simple — no schema migration risk, no on-disk leak surface, no per-user
data custody.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from sentinel.config import SentinelConfig
from sentinel.runner import RunResult, run_checks


ScanStatus = Literal["running", "complete", "error"]


class ScanState(BaseModel):
    """In-memory snapshot of one scan run.

    `config` is captured at scan start so the JSON export can reproduce the
    exact configuration that produced the findings, even if the file on
    disk is edited later. `config_path` is kept so the "Re-scan with same
    config" button can re-POST against the same file, picking up whatever
    edits have happened since.
    """

    id: str
    target: str  # base_url, for display in lists
    project: str
    config: SentinelConfig
    config_path: str
    status: ScanStatus = "running"
    started_at: datetime
    completed_at: datetime | None = None
    run_result: RunResult | None = None
    error: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status in ("complete", "error")

    @property
    def duration_ms(self) -> float | None:
        """Elapsed time in milliseconds. Live-updates while running."""
        end = self.completed_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds() * 1000


class ScanRunner:
    """Thread-safe (asyncio-safe) registry of running and completed scans."""

    def __init__(self) -> None:
        self._scans: dict[str, ScanState] = {}
        self._lock = asyncio.Lock()

    async def start(self, config: SentinelConfig, config_path: str) -> str:
        """Kick off a new scan as a background task. Returns the scan id."""
        scan_id = str(uuid.uuid4())
        state = ScanState(
            id=scan_id,
            target=config.meta.base_url,
            project=config.meta.project,
            config=config,
            config_path=config_path,
            started_at=datetime.now(timezone.utc),
        )
        async with self._lock:
            self._scans[scan_id] = state
        # Fire-and-forget — the task updates the registry on its own.
        asyncio.create_task(self._run(scan_id, config))
        return scan_id

    async def _run(self, scan_id: str, config: SentinelConfig) -> None:
        try:
            run_result = await run_checks(config)
            async with self._lock:
                state = self._scans.get(scan_id)
                if state is None:
                    return  # cleared between start and completion
                state.run_result = run_result
                state.status = "complete"
                state.completed_at = datetime.now(timezone.utc)
        except Exception as e:
            async with self._lock:
                state = self._scans.get(scan_id)
                if state is None:
                    return
                state.error = f"{type(e).__name__}: {e}"
                state.status = "error"
                state.completed_at = datetime.now(timezone.utc)

    async def get(self, scan_id: str) -> ScanState | None:
        async with self._lock:
            return self._scans.get(scan_id)

    async def list_recent(self, limit: int = 50) -> list[ScanState]:
        async with self._lock:
            scans = list(self._scans.values())
        scans.sort(key=lambda s: s.started_at, reverse=True)
        return scans[:limit]

    async def clear(self) -> None:
        """Drop all state. Primarily used by tests."""
        async with self._lock:
            self._scans.clear()


# Module-level singleton — one runner per process.
scan_runner = ScanRunner()
