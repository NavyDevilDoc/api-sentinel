"""LLM report generation.

Dispatches to configured LLM backend (gemini, claude, openai, ollama)
to produce a narrative security analysis from structured findings.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING

from sentinel.llm.prompt import build_prompt
from sentinel.reporter import build_report_data
from sentinel.utils.env_loader import EnvVarError, resolve_env_var

if TYPE_CHECKING:
    from sentinel.config import SentinelConfig
    from sentinel.runner import RunResult


class LLMBackendError(Exception):
    """Raised when the LLM backend is unavailable or fails."""


# Maps backend name to (module_path, env_var_name, pip_extra)
# env_var_name is None for backends that don't need an API key
# pip_extra is None for backends with no additional dependencies
BACKEND_REGISTRY: dict[str, tuple[str, str | None, str | None]] = {
    "gemini": ("sentinel.llm.gemini", "SENTINEL_GEMINI_KEY", "gemini"),
    "claude": ("sentinel.llm.claude", "SENTINEL_CLAUDE_KEY", "claude"),
    "openai": ("sentinel.llm.openai_backend", "SENTINEL_OPENAI_KEY", "openai"),
    "ollama": ("sentinel.llm.ollama", None, None),
}


async def generate_llm_report(
    run_result: RunResult,
    config: SentinelConfig,
    backend: str,
    redact_values: list[str] | None = None,
) -> str:
    """Generate a narrative LLM report from scan results.

    Args:
        run_result: The aggregate results from a sentinel run.
        config: The sentinel configuration.
        backend: LLM backend name (gemini, claude, openai, ollama).
        redact_values: Optional list of secret strings to scrub before sending.

    Returns:
        The narrative report text.

    Raises:
        LLMBackendError: If the backend is unavailable, misconfigured, or fails.
    """
    # Validate backend name
    if backend not in BACKEND_REGISTRY:
        raise LLMBackendError(
            f"Unknown LLM backend: '{backend}'. "
            f"Available: {', '.join(BACKEND_REGISTRY.keys())}"
        )

    module_path, env_var, pip_extra = BACKEND_REGISTRY[backend]

    # Lazy import the backend module
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        install_cmd = f"pip install api-sentinel[{pip_extra}]" if pip_extra else str(e)
        raise LLMBackendError(
            f"The '{backend}' backend requires additional dependencies. "
            f"Install them with: {install_cmd}"
        ) from e

    # Resolve API key (if needed)
    api_key: str | None = None
    if env_var is not None:
        try:
            api_key = resolve_env_var(
                env_var,
                description=f"API key for {backend} LLM backend",
            )
        except EnvVarError as e:
            raise LLMBackendError(
                f"API key not configured for '{backend}' backend. "
                f"Set the {env_var} environment variable."
            ) from e

    # Build the report data and prompt
    report_data = build_report_data(run_result, config, redact_values)
    findings_json = json.dumps(report_data["results"], indent=2)
    prompt = build_prompt(findings_json, report_data["summary"])

    # Call the backend
    try:
        return await mod.generate_report(prompt, api_key)
    except Exception as e:
        raise LLMBackendError(
            f"LLM backend '{backend}' failed: {e}"
        ) from e
