"""Atomic YAML writer for the config editor.

Save flow:
  1. Build the YAML text in memory.
  2. (Optional) Copy the existing file to `<path>.bak`.
  3. Write the new YAML to `<path>.tmp` in the same directory.
  4. `os.replace(tmp, path)` — atomic on POSIX and Windows.

The tmp-and-replace pattern guarantees the user never observes a
half-written config file, even on crash / power loss between steps.

Comment preservation: PyYAML strips comments on round-trip. The editor
surfaces this loss in the UI before save. v1 accepts the tradeoff;
ruamel.yaml could be introduced later if users complain.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


def write_config_yaml(
    path: Path,
    data: dict[str, Any],
    backup: bool = False,
) -> None:
    """Atomically write `data` as YAML to `path`.

    Args:
        path: Final destination for the config file.
        data: A plain dict (typically `pydantic_model.model_dump()`).
        backup: If True and `path` already exists, copy it to `<path>.bak`
            before writing.

    Raises:
        OSError: On I/O failure. The original file is unchanged because
            the rename is the last step.
    """
    path = Path(path)
    yaml_text = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml_text, encoding="utf-8")

    # os.replace is atomic on both POSIX and Windows when src/dst are on
    # the same filesystem. tmp is in the same directory as path, so we're
    # safe.
    import os

    os.replace(tmp, path)
