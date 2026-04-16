"""Google Gemini LLM backend.

Uses the google-generativeai SDK (synchronous, wrapped in asyncio.to_thread).
Requires SENTINEL_GEMINI_KEY environment variable.
"""

from __future__ import annotations

import asyncio

MODEL = "gemini-2.0-flash"


async def generate_report(prompt: str, api_key: str | None) -> str:
    """Generate a narrative report using Google Gemini.

    Args:
        prompt: The structured prompt string.
        api_key: Google AI API key.

    Returns:
        The LLM-generated narrative text.
    """
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL)

    response = await asyncio.to_thread(model.generate_content, prompt)
    return response.text
