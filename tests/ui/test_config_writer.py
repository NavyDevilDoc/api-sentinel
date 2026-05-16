"""Phase UI-5 — tests for the atomic YAML writer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")

from sentinel.ui.services.config_writer import write_config_yaml  # noqa: E402


_SAMPLE = {
    "meta": {
        "project": "test",
        "base_url": "https://api.example.com",
        "timeout_seconds": 10,
    },
    "auth": {"token_primary": "SENTINEL_TOK"},
    "endpoints": [{"path": "/a", "method": "GET"}],
    "checks": {"authorization": {"enabled": False}},
}


class TestWriteConfigYaml:
    def test_writes_file_with_yaml_content(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        write_config_yaml(target, _SAMPLE)
        assert target.exists()

        roundtrip = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert roundtrip == _SAMPLE

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("old: content\n", encoding="utf-8")

        write_config_yaml(target, _SAMPLE)
        roundtrip = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert roundtrip == _SAMPLE

    def test_no_backup_by_default(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("old: content\n", encoding="utf-8")

        write_config_yaml(target, _SAMPLE)
        assert not (tmp_path / "config.yaml.bak").exists()

    def test_backup_when_requested(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("old_marker: yes\n", encoding="utf-8")

        write_config_yaml(target, _SAMPLE, backup=True)

        bak = tmp_path / "config.yaml.bak"
        assert bak.exists()
        assert "old_marker" in bak.read_text(encoding="utf-8")

    def test_backup_skipped_when_target_did_not_exist(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "config.yaml"
        # target does not exist yet
        write_config_yaml(target, _SAMPLE, backup=True)
        assert target.exists()
        assert not (tmp_path / "config.yaml.bak").exists()

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        write_config_yaml(target, _SAMPLE)
        # tmp file should have been renamed atomically
        assert not (tmp_path / "config.yaml.tmp").exists()

    def test_preserves_key_order(self, tmp_path: Path) -> None:
        """sort_keys=False — meta should remain first, not alphabetized."""
        target = tmp_path / "config.yaml"
        write_config_yaml(target, _SAMPLE)
        text = target.read_text(encoding="utf-8")
        meta_pos = text.find("meta:")
        auth_pos = text.find("auth:")
        endpoints_pos = text.find("endpoints:")
        assert 0 <= meta_pos < auth_pos < endpoints_pos
