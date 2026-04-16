"""Structured prompt template for LLM security analysis.

The prompt is deterministic and structured — not freeform. It instructs
the LLM to produce exactly three sections: executive summary, prioritized
remediation steps, and business risk context for critical findings.
"""

from __future__ import annotations

import json

_PROMPT_TEMPLATE = """\
SYSTEM CONTEXT:
You are a senior API security analyst reviewing automated scan results from API Sentinel.
You will receive structured JSON findings from an automated security scan.
Your job is to produce a clear, actionable narrative report for a development team.

INSTRUCTIONS:
Produce exactly three sections:

1. EXECUTIVE SUMMARY
   - 2-3 sentences summarizing the overall security posture
   - State the total checks run, pass rate, and critical finding count
   - Give a one-sentence risk verdict

2. PRIORITIZED REMEDIATION STEPS
   - List each non-passing finding ordered by severity (critical first, then warning, then info)
   - For each: state what is wrong, why it matters, and the specific fix
   - Group related findings when the fix is the same

3. BUSINESS RISK CONTEXT
   - For each CRITICAL finding only: explain the business impact in non-technical terms
   - What could an attacker do? What data is at risk? What compliance implications exist?

CONSTRAINTS:
- Base your analysis ONLY on the findings JSON provided below
- Do not suggest re-testing or making additional HTTP calls
- Do not invent findings not present in the data
- Keep the total response under 1500 words
- Use markdown formatting for headers and lists

SCAN SUMMARY:
Total checks: {total}
Passed: {passed}
Critical: {critical}
Warnings: {warning}
Info: {info}

FINDINGS JSON:
{findings_json}
"""


def build_prompt(findings_json: str, summary: dict) -> str:
    """Build the structured LLM prompt from findings and summary counts.

    Args:
        findings_json: JSON string of the results list (redacted).
        summary: Dict with keys: total, passed, critical, warning, info.

    Returns:
        The complete prompt string ready to send to an LLM.
    """
    return _PROMPT_TEMPLATE.format(
        total=summary.get("total", 0),
        passed=summary.get("passed", 0),
        critical=summary.get("critical", 0),
        warning=summary.get("warning", 0),
        info=summary.get("info", 0),
        findings_json=findings_json,
    )
