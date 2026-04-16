"""Base check infrastructure.

Every security check produces CheckResult instances. This module defines
the result model, severity levels, the abstract base class for all check
modules, and a convenience factory for building results.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from sentinel.config import EndpointConfig

if TYPE_CHECKING:
    import httpx

    from sentinel.config import SentinelConfig


class Severity(str, Enum):
    """Severity level for a check finding."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    PASS = "pass"


class CheckResult(BaseModel):
    """The atom of the system. Every check produces exactly one of these."""

    check_id: str
    name: str
    severity: Severity
    passed: bool
    detail: str
    expected: str
    recommendation: str
    endpoint: str | None = None
    response_code: int | None = None
    latency_ms: float | None = None


class BaseCheck(ABC):
    """Abstract base class all check modules inherit from."""

    check_category: str  # e.g., "transport", "headers"

    @abstractmethod
    async def run(
        self,
        config: SentinelConfig,
        client: httpx.AsyncClient,
    ) -> list[CheckResult]:
        """Execute all checks in this category.

        Returns:
            A list of CheckResult instances, one per individual check.
        """
        ...


def make_result(
    check_id: str,
    name: str,
    *,
    passed: bool,
    detail: str,
    expected: str,
    recommendation: str = "",
    endpoint: str | None = None,
    response_code: int | None = None,
    latency_ms: float | None = None,
) -> CheckResult:
    """Convenience factory that auto-assigns severity from the passed flag.

    Passing checks get Severity.PASS. Failing checks default to
    Severity.CRITICAL — callers can override by constructing CheckResult
    directly when WARNING or INFO severity is appropriate.
    """
    severity = Severity.PASS if passed else Severity.CRITICAL
    return CheckResult(
        check_id=check_id,
        name=name,
        severity=severity,
        passed=passed,
        detail=detail,
        expected=expected,
        recommendation=recommendation,
        endpoint=endpoint,
        response_code=response_code,
        latency_ms=latency_ms,
    )


def endpoint_slug(path: str) -> str:
    """Convert a URL path to a slug for use in check_id.

    /resource/{id} -> resource_id
    /user/profile  -> user_profile
    /auth/login    -> auth_login
    """
    slug = path.lstrip("/")
    slug = slug.replace("/", "_")
    slug = slug.replace("{", "").replace("}", "")
    return slug


def resolve_path(endpoint: EndpointConfig) -> str | None:
    """Substitute path template placeholders with the first test_id.

    Returns None if the path has a placeholder but test_ids is empty.
    """
    if not re.search(r"\{[^}]+\}", endpoint.path):
        return endpoint.path

    if not endpoint.test_ids:
        return None

    return re.sub(r"\{[^}]+\}", str(endpoint.test_ids[0]), endpoint.path, count=1)
