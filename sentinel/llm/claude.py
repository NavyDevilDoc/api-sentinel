"""Anthropic Claude LLM backend.

Uses the anthropic SDK with native async support.
Requires SENTINEL_CLAUDE_KEY environment variable.
"""

from __future__ import annotations

MODEL = "claude-sonnet-4-20250514"


async def generate_report(prompt: str, api_key: str | None) -> str:
    """Generate a narrative report using Anthropic Claude.

    Args:
        prompt: The structured prompt string.
        api_key: Anthropic API key.

    Returns:
        The LLM-generated narrative text.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
