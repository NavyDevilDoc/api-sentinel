"""Unit tests for LLM report generation.

All tests work without any LLM SDK installed and without network access.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from sentinel.checks.base import CheckResult, Severity
from sentinel.config import SentinelConfig
from sentinel.llm import LLMBackendError, generate_llm_report
from sentinel.llm.prompt import build_prompt
from sentinel.runner import RunResult
from sentinel.utils.env_loader import EnvVarError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> SentinelConfig:
    return SentinelConfig.model_validate({
        "meta": {"project": "test-api", "base_url": "https://api.test.com"},
        "auth": {"token_primary": "TOK_A", "token_secondary": "TOK_B"},
        "endpoints": [],
        "checks": {"authorization": {"enabled": False}},
    })


def _make_run_result(results: list[CheckResult] | None = None) -> RunResult:
    return RunResult(
        results=results or [],
        timestamp=datetime.now(timezone.utc),
        duration_ms=12.34,
        checks_run=["transport", "headers"],
        checks_skipped=[],
    )


def _make_sample_results() -> list[CheckResult]:
    return [
        CheckResult(
            check_id="headers.xpoweredby",
            name="X-Powered-By leaks",
            severity=Severity.CRITICAL,
            passed=False,
            detail="X-Powered-By: Express",
            expected="Header absent",
            recommendation="Remove X-Powered-By header",
        ),
        CheckResult(
            check_id="transport.https_enforced",
            name="HTTPS enforced",
            severity=Severity.PASS,
            passed=True,
            detail="https:// scheme",
            expected="https://",
            recommendation="",
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt Tests
# ---------------------------------------------------------------------------


class TestPrompt:

    def test_prompt_contains_executive_summary_instruction(self) -> None:
        prompt = build_prompt("[]", {"total": 0, "passed": 0, "critical": 0, "warning": 0, "info": 0})
        assert "EXECUTIVE SUMMARY" in prompt

    def test_prompt_contains_remediation_instruction(self) -> None:
        prompt = build_prompt("[]", {"total": 0, "passed": 0, "critical": 0, "warning": 0, "info": 0})
        assert "PRIORITIZED REMEDIATION" in prompt

    def test_prompt_contains_business_risk_instruction(self) -> None:
        prompt = build_prompt("[]", {"total": 0, "passed": 0, "critical": 0, "warning": 0, "info": 0})
        assert "BUSINESS RISK CONTEXT" in prompt

    def test_prompt_includes_findings_json(self) -> None:
        findings = json.dumps([{"check_id": "test.check", "detail": "test detail"}])
        prompt = build_prompt(findings, {"total": 1, "passed": 0, "critical": 1, "warning": 0, "info": 0})
        assert "test.check" in prompt
        assert "test detail" in prompt

    def test_prompt_includes_severity_counts(self) -> None:
        summary = {"total": 10, "passed": 7, "critical": 2, "warning": 1, "info": 0}
        prompt = build_prompt("[]", summary)
        assert "Total checks: 10" in prompt
        assert "Passed: 7" in prompt
        assert "Critical: 2" in prompt

    def test_prompt_word_limit_instruction(self) -> None:
        prompt = build_prompt("[]", {"total": 0, "passed": 0, "critical": 0, "warning": 0, "info": 0})
        assert "1500 words" in prompt


# ---------------------------------------------------------------------------
# Dispatcher Tests
# ---------------------------------------------------------------------------


def _make_mock_backend(return_text: str = "Mock LLM narrative") -> ModuleType:
    """Create a mock backend module with an async generate_report function."""
    mock_mod = MagicMock(spec=ModuleType)
    mock_mod.generate_report = AsyncMock(return_value=return_text)
    return mock_mod


class TestDispatcher:
    """Tests for the LLM dispatcher.

    Uses patch.dict("sys.modules") to inject mock backend modules, avoiding
    interference with importlib.import_module's internal resolution.
    """

    @pytest.mark.asyncio
    async def test_routes_to_correct_backend(self) -> None:
        mock_mod = _make_mock_backend("Gemini response")
        with patch.dict("sys.modules", {"sentinel.llm.gemini": mock_mod}), \
             patch("sentinel.llm.resolve_env_var", return_value="fake-key"):
            result = await generate_llm_report(
                _make_run_result(_make_sample_results()),
                _make_config(), "gemini",
            )
        assert result == "Gemini response"
        mock_mod.generate_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_sdk_raises_clear_error(self) -> None:
        # Remove the module from sys.modules to force ImportError
        with patch.dict("sys.modules", {"sentinel.llm.claude": None}):
            with pytest.raises(LLMBackendError, match="pip install api-sentinel"):
                await generate_llm_report(
                    _make_run_result(), _make_config(), "claude",
                )

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_clear_error(self) -> None:
        mock_mod = _make_mock_backend()
        with patch.dict("sys.modules", {"sentinel.llm.claude": mock_mod}), \
             patch("sentinel.llm.resolve_env_var", side_effect=EnvVarError("SENTINEL_CLAUDE_KEY")):
            with pytest.raises(LLMBackendError, match="SENTINEL_CLAUDE_KEY"):
                await generate_llm_report(
                    _make_run_result(), _make_config(), "claude",
                )

    @pytest.mark.asyncio
    async def test_unknown_backend_raises_error(self) -> None:
        with pytest.raises(LLMBackendError, match="Unknown LLM backend"):
            await generate_llm_report(
                _make_run_result(), _make_config(), "nonexistent",
            )

    @pytest.mark.asyncio
    async def test_ollama_no_api_key_needed(self) -> None:
        mock_mod = _make_mock_backend("Ollama response")
        with patch.dict("sys.modules", {"sentinel.llm.ollama": mock_mod}):
            result = await generate_llm_report(
                _make_run_result(_make_sample_results()),
                _make_config(), "ollama",
            )
        assert result == "Ollama response"

    @pytest.mark.asyncio
    async def test_backend_exception_wrapped(self) -> None:
        mock_mod = _make_mock_backend()
        mock_mod.generate_report = AsyncMock(side_effect=RuntimeError("API error"))
        with patch.dict("sys.modules", {"sentinel.llm.gemini": mock_mod}), \
             patch("sentinel.llm.resolve_env_var", return_value="fake-key"):
            with pytest.raises(LLMBackendError, match="API error"):
                await generate_llm_report(
                    _make_run_result(), _make_config(), "gemini",
                )

    @pytest.mark.asyncio
    async def test_redaction_applied_before_prompt(self) -> None:
        """Token values should be redacted in the prompt sent to the LLM."""
        mock_mod = _make_mock_backend()
        secret = "super-secret-token"

        results = [CheckResult(
            check_id="test.check", name="Test", severity=Severity.CRITICAL,
            passed=False, detail=f"Leaked: {secret}", expected="none",
            recommendation="fix",
        )]

        with patch.dict("sys.modules", {"sentinel.llm.gemini": mock_mod}), \
             patch("sentinel.llm.resolve_env_var", return_value="fake-key"):
            await generate_llm_report(
                _make_run_result(results), _make_config(), "gemini",
                redact_values=[secret],
            )

        call_args = mock_mod.generate_report.call_args
        prompt_sent = call_args[0][0]
        assert secret not in prompt_sent
        assert "[REDACTED]" in prompt_sent


# ---------------------------------------------------------------------------
# Ollama Backend Tests
# ---------------------------------------------------------------------------


class TestOllamaBackend:

    @pytest.mark.asyncio
    @respx.mock
    async def test_ollama_posts_to_localhost(self) -> None:
        from sentinel.llm.ollama import generate_report

        route = respx.post("http://localhost:11434/api/generate").mock(
            return_value=httpx.Response(200, json={"response": "Ollama says hello"})
        )
        result = await generate_report("test prompt", None)
        assert result == "Ollama says hello"
        assert route.called
        body = json.loads(route.calls[0].request.content)
        assert body["prompt"] == "test prompt"
        assert body["model"] == "llama3.2"
        assert body["stream"] is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_ollama_connection_refused(self) -> None:
        from sentinel.llm.ollama import generate_report

        respx.post("http://localhost:11434/api/generate").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(httpx.ConnectError, match="ollama serve"):
            await generate_report("test prompt", None)


# ---------------------------------------------------------------------------
# Empty Results Edge Case
# ---------------------------------------------------------------------------


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_results_still_generates(self) -> None:
        """An empty findings list should still produce a prompt and call the backend."""
        mock_mod = _make_mock_backend("No findings report")
        with patch.dict("sys.modules", {"sentinel.llm.gemini": mock_mod}), \
             patch("sentinel.llm.resolve_env_var", return_value="fake-key"):
            result = await generate_llm_report(
                _make_run_result(), _make_config(), "gemini",
            )
        assert result == "No findings report"
        mock_mod.generate_report.assert_called_once()
