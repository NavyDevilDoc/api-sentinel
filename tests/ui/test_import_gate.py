"""Phase UI-0 — verify the sentinel.ui import gate behavior.

The gate must:
  1. Allow `import sentinel.ui` cleanly when FastAPI is present.
  2. Raise ImportError with a clear install hint when FastAPI is absent.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_sentinel_ui_imports_when_fastapi_present() -> None:
    pytest.importorskip("fastapi")
    sys.modules.pop("sentinel.ui", None)
    import sentinel.ui  # noqa: F401


def test_sentinel_ui_raises_install_hint_when_fastapi_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "fastapi", None)
    monkeypatch.delitem(sys.modules, "sentinel.ui", raising=False)

    with pytest.raises(ImportError, match="pip install"):
        importlib.import_module("sentinel.ui")
