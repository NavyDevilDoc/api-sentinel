"""Form data parsing for the config editor.

Browsers post forms as flat key/value pairs. The config schema is nested,
with lists and booleans. This module converts the flat form representation
back into a nested dict that pydantic can validate.

Naming convention used in form `name` attributes:
  - Dots separate nested keys:   `meta.project`, `checks.transport.enabled`
  - Brackets index list items:   `endpoints[0].path`, `endpoints[0].method`

Booleans use the two-input pattern (a hidden `value="false"` immediately
followed by a checkbox `value="true"`). Browsers submit only the checked
input, but when checked the multi-dict has both — `dict(form)` then takes
the latter (`"true"`). When unchecked the form has only the hidden
(`"false"`). Pydantic coerces both strings to bool.

For pydantic validation errors, `loc_to_field_name()` turns a `loc` tuple
like `("endpoints", 0, "path")` into a form-field-name string like
`endpoints[0].path` so the template can match errors to fields.
"""

from __future__ import annotations

import re
from typing import Any

_LIST_INDEX_RE = re.compile(r"^(\w+)\[(\d+)\]$")


def parse_form_to_dict(form: dict[str, str]) -> dict[str, Any]:
    """Convert a flat form dict to a nested structure.

    `endpoints[0].path = "/foo"` becomes
    `{"endpoints": [{"path": "/foo"}]}`.
    """
    result: dict[str, Any] = {}
    for key, value in form.items():
        _set_nested(result, key, value)
    return result


def _set_nested(target: dict[str, Any], key: str, value: str) -> None:
    parts = key.split(".")
    current: Any = target

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        match = _LIST_INDEX_RE.match(part)

        if match:
            name = match.group(1)
            idx = int(match.group(2))
            if not isinstance(current, dict):
                # Inconsistent input — bail silently to avoid crashing the
                # editor on malformed posts.
                return
            current.setdefault(name, [])
            while len(current[name]) <= idx:
                current[name].append({})
            if is_last:
                current[name][idx] = value
            else:
                if not isinstance(current[name][idx], dict):
                    current[name][idx] = {}
                current = current[name][idx]
        else:
            if not isinstance(current, dict):
                return
            if is_last:
                current[part] = value
            else:
                current.setdefault(part, {})
                if not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]


def split_lines(text: str | None) -> list[str]:
    """Split a textarea string into a list of non-empty trimmed lines."""
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def split_csv_ints(text: str | None) -> list[int | str]:
    """Split a comma-separated string into ints where possible, else strs.

    The `EndpointConfig.test_ids` schema accepts `list[int | str]`. We try
    int first so numeric ids stay numeric, but accept string ids (uuids,
    slugs) as a fallback.
    """
    if not text:
        return []
    out: list[int | str] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            out.append(token)
    return out


def loc_to_field_name(loc: tuple[Any, ...]) -> str:
    """Convert a pydantic error `loc` tuple to a dotted form field name.

    `("meta", "base_url")`              -> `"meta.base_url"`
    `("endpoints", 0, "path")`          -> `"endpoints[0].path"`
    """
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}" if parts else str(item))
    return "".join(parts)
