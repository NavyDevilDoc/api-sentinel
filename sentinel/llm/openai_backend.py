"""OpenAI LLM backend.

Uses the openai SDK with native async support.
Requires SENTINEL_OPENAI_KEY environment variable.
Module named openai_backend to avoid shadowing the openai package.
"""

from __future__ import annotations

MODEL = "gpt-4o-mini"


async def generate_report(prompt: str, api_key: str | None) -> str:
    """Generate a narrative report using OpenAI.

    Args:
        prompt: The structured prompt string.
        api_key: OpenAI API key.

    Returns:
        The LLM-generated narrative text.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    return response.choices[0].message.content
