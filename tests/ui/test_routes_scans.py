"""Phase UI-6 — tests for the scan routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from sentinel.checks.base import CheckResult, Severity  # noqa: E402
from sentinel.config import (  # noqa: E402
    AuthConfig,
    MetaConfig,
    SentinelConfig,
)
from sentinel.runner import RunResult  # noqa: E402
from sentinel.ui.server import create_app  # noqa: E402
from sentinel.ui.services import scan_runner as scan_runner_mod  # noqa: E402


def _make_config() -> SentinelConfig:
    return SentinelConfig(
        meta=MetaConfig(project="test-proj", base_url="https://example.com"),
        auth=AuthConfig(token_primary="SENTINEL_TEST_TOK"),
        endpoints=[],
        checks={"authorization": {"enabled": False}},  # type: ignore[arg-type]
    )


_GOOD_YAML = (
    "meta:\n"
    "  project: route-test\n"
    "  base_url: https://api.example.com\n"
    "auth:\n"
    "  token_primary: SENTINEL_TOK\n"
    "endpoints: []\n"
    "checks:\n"
    "  authorization:\n"
    "    enabled: false\n"
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
            CheckResult(
                check_id="headers.xpoweredby",
                name="X-Powered-By absent",
                severity=Severity.CRITICAL,
                passed=False,
                detail="X-Powered-By: Express 4.18",
                expected="header absent",
                recommendation="Disable header in your framework",
                endpoint="/",
            ),
        ],
        timestamp=datetime.now(timezone.utc),
        duration_ms=42.0,
        checks_run=["transport", "headers"],
        checks_skipped=[],
    )


@pytest.fixture(autouse=True)
def _clear_singleton() -> None:
    """Reset the module-level singleton between tests to avoid state bleed.
    Direct dict access bypasses the asyncio.Lock — fine here because tests
    are single-threaded, and using a sync fixture avoids the unawaited-
    coroutine warning that would come from declaring this async."""
    scan_runner_mod.scan_runner._scans.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def good_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "sentinel_config.yaml"
    path.write_text(_GOOD_YAML, encoding="utf-8")
    return path


@pytest.fixture
def stubbed_run_checks(monkeypatch: pytest.MonkeyPatch):
    """Replace runner.run_checks with a fast deterministic stub."""
    async def fake(config, selected=None):
        return _make_run_result()
    monkeypatch.setattr(scan_runner_mod, "run_checks", fake)
    return fake


# ---------------------------------------------------------------------------
# GET /scans
# ---------------------------------------------------------------------------


class TestListScans:
    def test_empty_state_renders(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.get("/scans")
        assert response.status_code == 200
        assert "No scans yet" in response.text

    def test_includes_new_scan_form(self, client: TestClient) -> None:
        response = client.get("/scans")
        assert 'action="/scans"' in response.text
        assert 'method="post"' in response.text


# ---------------------------------------------------------------------------
# POST /scans
# ---------------------------------------------------------------------------


class TestStartScan:
    def test_valid_config_starts_scan_and_redirects(
        self,
        client: TestClient,
        good_config: Path,
        stubbed_run_checks,
    ) -> None:
        response = client.post("/scans", data={}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/scans/")

    def test_missing_config_returns_error(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.post("/scans", data={}, follow_redirects=False)
        assert response.status_code == 400
        assert "Cannot scan" in response.text

    def test_path_traversal_returns_400(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        response = client.post(
            "/scans?path=../escape.yaml", data={}, follow_redirects=False,
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# GET /scans/{id}
# ---------------------------------------------------------------------------


class TestViewScan:
    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.get("/scans/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert "not found" in response.text.lower()

    def test_view_after_start_shows_target(
        self,
        client: TestClient,
        good_config: Path,
        stubbed_run_checks,
    ) -> None:
        # Start a scan, follow the redirect
        start_resp = client.post("/scans", data={}, follow_redirects=False)
        scan_id_path = start_resp.headers["location"]
        # Give the background task a moment to either finish or not — both
        # states are valid for this assertion.
        import time
        time.sleep(0.1)

        detail = client.get(scan_id_path)
        assert detail.status_code == 200
        assert "https://api.example.com" in detail.text
        assert "route-test" in detail.text


# ---------------------------------------------------------------------------
# GET /scans/{id}/status — the polling fragment
# ---------------------------------------------------------------------------


class TestScanStatus:
    def test_status_fragment_for_unknown_id_404s(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/scans/00000000-0000-0000-0000-000000000000/status"
        )
        assert response.status_code == 404

    def test_completed_scan_status_includes_results(
        self,
        client: TestClient,
        good_config: Path,
        stubbed_run_checks,
    ) -> None:
        start = client.post("/scans", data={}, follow_redirects=False)
        scan_id = start.headers["location"].split("/")[-1]

        # Poll briefly until the background task completes
        import time
        for _ in range(50):
            status = client.get(f"/scans/{scan_id}/status")
            if "Running" not in status.text:
                break
            time.sleep(0.05)

        status = client.get(f"/scans/{scan_id}/status")
        assert status.status_code == 200
        # When complete, the polling trigger should be GONE
        assert "hx-trigger" not in status.text
        # Severity counts should be visible
        assert "Critical" in status.text
        assert "Passed" in status.text
        # Critical finding should appear
        assert "X-Powered-By" in status.text

    def test_running_scan_status_includes_polling_trigger(
        self,
        client: TestClient,
        good_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A scan that's blocked on a never-returning task must still
        return a fragment with hx-trigger so the browser keeps polling."""
        stop_event = asyncio.Event()

        async def slow_run(config, selected=None):
            await stop_event.wait()
            return _make_run_result()

        monkeypatch.setattr(scan_runner_mod, "run_checks", slow_run)

        start = client.post("/scans", data={}, follow_redirects=False)
        scan_id = start.headers["location"].split("/")[-1]

        status = client.get(f"/scans/{scan_id}/status")
        assert status.status_code == 200
        # Polling is active
        assert 'hx-trigger="every 1s"' in status.text
        assert "Running" in status.text

        # Release the blocked task so it doesn't dangle
        stop_event.set()

    def test_status_fragment_running_badge(self, client: TestClient) -> None:
        """Regression: the polled fragment must include the current status
        badge. The original bug had the badge in the static parent page's
        <dl> outside the swap target — completing scans displayed
        'RUNNING' forever."""
        from sentinel.ui.services.scan_runner import ScanState

        scan_runner_mod.scan_runner._scans["test-running-id"] = ScanState(
            id="test-running-id",
            target="https://example.com",
            project="test-proj",
            config=_make_config(),
            config_path="/tmp/test.yaml",
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        text = client.get("/scans/test-running-id/status").text
        assert 'class="status-badge status-running"' in text

    def test_status_fragment_complete_badge(self, client: TestClient) -> None:
        """Companion to the running-badge test — same regression, but
        verifies the badge transitions to 'complete' (not stale 'running')
        once the scan is done."""
        from sentinel.ui.services.scan_runner import ScanState

        scan_runner_mod.scan_runner._scans["test-complete-id"] = ScanState(
            id="test-complete-id",
            target="https://example.com",
            project="test-proj",
            config=_make_config(),
            config_path="/tmp/test.yaml",
            status="complete",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            run_result=_make_run_result(),
        )

        text = client.get("/scans/test-complete-id/status").text
        assert 'class="status-badge status-complete"' in text
        # No stale 'running' badge anywhere in the fragment
        assert "status-running" not in text


# ---------------------------------------------------------------------------
# GET /scans/{id}/results — severity filter (Phase UI-7)
# ---------------------------------------------------------------------------


class TestScanResultsFilter:
    def _seed_completed_scan(
        self, *, scan_id: str, results: list[CheckResult]
    ) -> None:
        from sentinel.ui.services.scan_runner import ScanState

        scan_runner_mod.scan_runner._scans[scan_id] = ScanState(
            id=scan_id,
            target="https://example.com",
            project="test",
            config=_make_config(),
            config_path="/tmp/test.yaml",
            status="complete",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            run_result=RunResult(
                results=results,
                timestamp=datetime.now(timezone.utc),
                duration_ms=10.0,
                checks_run=["transport"],
                checks_skipped=[],
            ),
        )

    def _r(self, check_id: str, severity: Severity, passed: bool) -> CheckResult:
        return CheckResult(
            check_id=check_id,
            # Use the full check_id as the name so it appears in rendered HTML
            # via the template's `{{ r.name }}` — makes filter assertions
            # straightforward.
            name=check_id,
            severity=severity,
            passed=passed,
            detail="d",
            expected="e",
            recommendation="r",
        )

    def test_filter_critical_keeps_only_critical(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan(
            scan_id="flt-1",
            results=[
                self._r("transport.a", Severity.CRITICAL, False),
                self._r("transport.b", Severity.WARNING, False),
                self._r("transport.c", Severity.PASS, True),
            ],
        )
        text = client.get("/scans/flt-1/results?filter=critical").text
        assert "transport.a" in text
        assert "transport.b" not in text
        assert "transport.c" not in text

    def test_filter_warning_keeps_critical_and_warning(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan(
            scan_id="flt-2",
            results=[
                self._r("transport.a", Severity.CRITICAL, False),
                self._r("transport.b", Severity.WARNING, False),
                self._r("transport.c", Severity.PASS, True),
            ],
        )
        text = client.get("/scans/flt-2/results?filter=warning").text
        assert "transport.a" in text
        assert "transport.b" in text
        assert "transport.c" not in text

    def test_filter_pass_keeps_only_passing(self, client: TestClient) -> None:
        self._seed_completed_scan(
            scan_id="flt-3",
            results=[
                self._r("transport.a", Severity.CRITICAL, False),
                self._r("transport.c", Severity.PASS, True),
            ],
        )
        text = client.get("/scans/flt-3/results?filter=pass").text
        assert "transport.a" not in text
        assert "transport.c" in text

    def test_filter_all_keeps_everything(self, client: TestClient) -> None:
        self._seed_completed_scan(
            scan_id="flt-4",
            results=[
                self._r("transport.a", Severity.CRITICAL, False),
                self._r("transport.b", Severity.WARNING, False),
                self._r("transport.c", Severity.PASS, True),
            ],
        )
        text = client.get("/scans/flt-4/results?filter=all").text
        assert "transport.a" in text
        assert "transport.b" in text
        assert "transport.c" in text

    def test_unknown_filter_falls_back_to_all(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan(
            scan_id="flt-5",
            results=[self._r("transport.a", Severity.CRITICAL, False)],
        )
        text = client.get("/scans/flt-5/results?filter=nonsense").text
        assert "transport.a" in text
        assert 'class="btn-filter btn-active"' in text  # All button is active

    def test_active_filter_button_has_active_class(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan(
            scan_id="flt-6",
            results=[self._r("transport.a", Severity.CRITICAL, False)],
        )
        text = client.get("/scans/flt-6/results?filter=critical").text
        # The Critical button should carry .btn-active
        crit_block = text[
            text.find("filter=critical"): text.find("filter=critical") + 400
        ]
        assert "btn-active" in crit_block

    def test_filter_unknown_scan_404s(self, client: TestClient) -> None:
        response = client.get(
            "/scans/00000000-0000-0000-0000-000000000000/results"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /scans/{id}/export.json — JSON download (Phase UI-7)
# ---------------------------------------------------------------------------


class TestScanExport:
    def _seed_completed_scan(self, scan_id: str) -> None:
        from sentinel.ui.services.scan_runner import ScanState

        scan_runner_mod.scan_runner._scans[scan_id] = ScanState(
            id=scan_id,
            target="https://example.com",
            project="export-test",
            config=_make_config(),
            config_path="/tmp/test.yaml",
            status="complete",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            run_result=_make_run_result(),
        )

    def test_export_returns_json_with_disposition_header(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan("exp-1")
        response = client.get("/scans/exp-1/export.json")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "attachment" in response.headers["content-disposition"]
        assert "sentinel-scan-exp-1" in response.headers["content-disposition"]

    def test_export_body_has_expected_structure(
        self, client: TestClient
    ) -> None:
        self._seed_completed_scan("exp-2")
        body = client.get("/scans/exp-2/export.json").json()
        assert "meta" in body
        assert "summary" in body
        assert "results" in body
        assert "results_by_category" in body
        # `build_report_data` uses config.meta.project, not scan.project.
        # Our _make_config() helper sets project="test-proj".
        assert body["meta"]["project"] == "test-proj"

    def test_export_redacts_token_values(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Token VALUES from env vars must be replaced with [REDACTED] in
        the exported JSON — same contract as the CLI's --output json."""
        canary = "ZZZ-export-redact-canary-ZZZ"
        monkeypatch.setenv("SENTINEL_TEST_TOK", canary)

        # Seed a scan whose result.detail contains the canary value
        from sentinel.ui.services.scan_runner import ScanState

        rr = RunResult(
            results=[
                CheckResult(
                    check_id="auth.probe",
                    name="auth probe",
                    severity=Severity.CRITICAL,
                    passed=False,
                    detail=f"echoed token: {canary}",
                    expected="no echo",
                    recommendation="strip",
                ),
            ],
            timestamp=datetime.now(timezone.utc),
            duration_ms=1.0,
            checks_run=["auth"],
            checks_skipped=[],
        )

        scan_runner_mod.scan_runner._scans["exp-redact"] = ScanState(
            id="exp-redact",
            target="https://example.com",
            project="redact-test",
            config=_make_config(),
            config_path="/tmp/test.yaml",
            status="complete",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            run_result=rr,
        )

        response_text = client.get("/scans/exp-redact/export.json").text
        assert canary not in response_text
        assert "[REDACTED]" in response_text

    def test_export_unknown_scan_404s(self, client: TestClient) -> None:
        response = client.get(
            "/scans/00000000-0000-0000-0000-000000000000/export.json"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Buttons on the results panel (Phase UI-7)
# ---------------------------------------------------------------------------


class TestResultsPanelButtons:
    def test_results_partial_includes_download_link_and_rescan_form(
        self, client: TestClient
    ) -> None:
        from sentinel.ui.services.scan_runner import ScanState

        scan_runner_mod.scan_runner._scans["btn-test"] = ScanState(
            id="btn-test",
            target="https://example.com",
            project="btn",
            config=_make_config(),
            config_path="/some/path/sentinel_config.yaml",
            status="complete",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            run_result=_make_run_result(),
        )

        text = client.get("/scans/btn-test/status").text
        # Download link
        assert "/scans/btn-test/export.json" in text
        # Re-scan form — should target the original config_path
        assert 'action="/scans?path=' in text
        assert "sentinel_config.yaml" in text
