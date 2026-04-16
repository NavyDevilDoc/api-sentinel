"""Ollama local LLM backend.

Uses httpx to POST to the local Ollama API (no SDK required).
No API key needed — Ollama runs locally via `ollama serve`.
"""

from __future__ import annotations

import httpx

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.2"


async def generate_report(prompt: str, api_key: str | None) -> str:
    """Generate a narrative report using a local Ollama instance.

    Args:
        prompt: The structured prompt string.
        api_key: Ignored (Ollama does not require an API key).

    Returns:
        The LLM-generated narrative text.

    Raises:
        httpx.ConnectError: If Ollama is not running at localhost:11434.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        try:
            response = await client.post(
                OLLAMA_URL,
                json={
                    "model": DEFAULT_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            return response.json()["response"]
        except httpx.ConnectError as e:
            raise httpx.ConnectError(
                "Cannot connect to Ollama at localhost:11434. "
                "Is Ollama running? Start it with: ollama serve"
            ) from e
