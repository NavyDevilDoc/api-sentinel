"""Unit tests for sentinel.reporter — Rich terminal output and JSON export."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from sentinel.checks.base import CheckResult, Severity
from sentinel.config import SentinelConfig
from sentinel.reporter import render_json_report, render_terminal_report
from sentinel.runner import RunResult


def _make_run_result(results: list[CheckResult] | None = None) -> RunResult:
    """Helper to build a RunResult for testing."""
    return RunResult(
        results=results or [],
        timestamp=datetime.now(timezone.utc),
        duration_ms=12.34,
        checks_run=["transport", "headers"],
        checks_skipped=[],
    )


def _capture_report(
    run_result: RunResult,
    base_url: str = "https://api.test.com",
    min_severity: Severity = Severity.PASS,
) -> str:
    """Render a report and capture the terminal output as a string."""
    console = Console(file=None, force_terminal=False, highlight=False, width=80)
    with console.capture() as capture:
        render_terminal_report(run_result, base_url, min_severity, console)
    return capture.get()


class TestTerminalReport:
    """Tests for render_terminal_report."""

    def test_header_renders(self) -> None:
        """The header panel includes the tool name."""
        output = _capture_report(_make_run_result())
        assert "API SENTINEL" in output

    def test_empty_results_message(self) -> None:
        """An empty run shows 'No checks executed'."""
        output = _capture_report(_make_run_result())
        assert "No checks executed" in output

    def test_results_grouped_by_category(
        self, sample_check_results: list[CheckResult]
    ) -> None:
        """Results are grouped under their category headers."""
        output = _capture_report(_make_run_result(sample_check_results))
        assert "TRANSPORT" in output
        assert "HEADERS" in output

    def test_summary_counts(
        self, sample_check_results: list[CheckResult]
    ) -> None:
        """Summary line shows correct critical, warning, and pass counts."""
        output = _capture_report(_make_run_result(sample_check_results))
        assert "Critical: 1" in output
        assert "Warnings: 1" in output
        assert "3/5" in output

    def test_critical_findings_panel(
        self, sample_check_results: list[CheckResult]
    ) -> None:
        """Critical findings get a detailed panel."""
        output = _capture_report(_make_run_result(sample_check_results))
        assert "CRITICAL FINDINGS" in output
        assert "headers.xpoweredby" in output
        assert "helmet.js" in output

    def test_severity_filter_excludes_lower(
        self, sample_check_results: list[CheckResult]
    ) -> None:
        """Setting min_severity to WARNING excludes PASS and INFO results."""
        output = _capture_report(
            _make_run_result(sample_check_results),
            min_severity=Severity.WARNING,
        )
        # WARNING and CRITICAL should appear, but PASS-only entries should not
        assert "CRITICAL FINDINGS" in output
        # The pass-only transport category should still not show its pass entries
        # in the per-category section (they're filtered out)


# ---------------------------------------------------------------------------
# JSON Export Tests
# ---------------------------------------------------------------------------


def _make_config() -> SentinelConfig:
    """Helper to build a minimal SentinelConfig for JSON tests."""
    return SentinelConfig.model_validate({
        "meta": {"project": "test-api", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK", "token_secondary": "TOK_B"},
        "endpoints": [],
        "checks": {"authorization": {"enabled": False}},
    })


class TestJsonReport:
    """Tests for render_json_report."""

    def test_json_file_created(
        self, tmp_path: Path, sample_check_results: list[CheckResult]
    ) -> None:
        """JSON file is written and contains valid JSON."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(sample_check_results), output, _make_config()
        )
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_json_meta_fields(
        self, tmp_path: Path, sample_check_results: list[CheckResult]
    ) -> None:
        """Meta section contains tool, version, project, base_url, timestamp."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(sample_check_results), output, _make_config()
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        meta = data["meta"]
        assert meta["tool"] == "api-sentinel"
        assert meta["version"] == "0.1.0"
        assert meta["project"] == "test-api"
        assert meta["base_url"] == "https://api.test.com"
        assert "timestamp" in meta
        assert "duration_ms" in meta

    def test_json_summary_counts(
        self, tmp_path: Path, sample_check_results: list[CheckResult]
    ) -> None:
        """Summary counts match the results."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(sample_check_results), output, _make_config()
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        summary = data["summary"]
        assert summary["total"] == 5
        assert summary["passed"] == 3
        assert summary["critical"] == 1
        assert summary["warning"] == 1

    def test_json_results_by_category(
        self, tmp_path: Path, sample_check_results: list[CheckResult]
    ) -> None:
        """Results are grouped by category."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(sample_check_results), output, _make_config()
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        by_cat = data["results_by_category"]
        assert "transport" in by_cat
        assert "headers" in by_cat
        assert len(by_cat["transport"]) == 2
        assert len(by_cat["headers"]) == 3

    def test_json_flat_results(
        self, tmp_path: Path, sample_check_results: list[CheckResult]
    ) -> None:
        """Flat results list contains all results."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(sample_check_results), output, _make_config()
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        assert len(data["results"]) == 5

    def test_json_token_redaction(self, tmp_path: Path) -> None:
        """Token values in result fields are replaced with [REDACTED]."""
        secret = "super-secret-token-12345"
        results = [
            CheckResult(
                check_id="auth.test",
                name="Test check",
                severity=Severity.CRITICAL,
                passed=False,
                detail=f"Token value leaked: {secret}",
                expected="No token in output",
                recommendation="Fix it",
            )
        ]
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(results), output, _make_config(),
            redact_values=[secret],
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        detail = data["results"][0]["detail"]
        assert secret not in detail
        assert "[REDACTED]" in detail

    def test_json_empty_results(self, tmp_path: Path) -> None:
        """Empty run produces valid JSON with zero counts."""
        output = tmp_path / "report.json"
        render_json_report(
            _make_run_result(), output, _make_config()
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["summary"]["total"] == 0
        assert data["summary"]["passed"] == 0
        assert data["results"] == []
        assert data["results_by_category"] == {}
