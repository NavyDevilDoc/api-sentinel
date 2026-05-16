"""Phase UI-1 — tests for subcommand dispatch and argv normalization.

Verifies:
  - Bare `sentinel ...` keeps working (gets `scan` injected) for v0.1.0 compat.
  - Explicit subcommands (`scan`, `ui`, `init`) route correctly.
  - Help/-h is preserved at the top level.
  - The stubs for `ui` and `init` exit cleanly with a clear message.
"""

from __future__ import annotations

import pytest

from sentinel.cli import (
    EXIT_OK,
    _normalize_argv,
    build_parser,
    main,
)


# ---------------------------------------------------------------------------
# _normalize_argv — backward compatibility shim
# ---------------------------------------------------------------------------


class TestNormalizeArgv:
    def test_empty_argv_becomes_scan(self) -> None:
        assert _normalize_argv([]) == ["scan"]

    def test_bare_flag_gets_scan_injected(self) -> None:
        assert _normalize_argv(["--config", "foo.yaml"]) == [
            "scan",
            "--config",
            "foo.yaml",
        ]

    def test_explicit_scan_left_alone(self) -> None:
        assert _normalize_argv(["scan", "--config", "foo.yaml"]) == [
            "scan",
            "--config",
            "foo.yaml",
        ]

    def test_ui_subcommand_left_alone(self) -> None:
        assert _normalize_argv(["ui", "--port", "8080"]) == ["ui", "--port", "8080"]

    def test_init_subcommand_left_alone(self) -> None:
        assert _normalize_argv(["init", "--spec", "openapi.json"]) == [
            "init",
            "--spec",
            "openapi.json",
        ]

    def test_help_short_left_alone(self) -> None:
        assert _normalize_argv(["-h"]) == ["-h"]

    def test_help_long_left_alone(self) -> None:
        assert _normalize_argv(["--help"]) == ["--help"]


# ---------------------------------------------------------------------------
# build_parser — subparser wiring
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_scan_subcommand_routes(self) -> None:
        args = build_parser().parse_args(["scan"])
        assert args.command == "scan"

    def test_ui_subcommand_routes(self) -> None:
        args = build_parser().parse_args(["ui"])
        assert args.command == "ui"

    def test_init_subcommand_routes(self) -> None:
        args = build_parser().parse_args(["init"])
        assert args.command == "init"

    def test_scan_carries_all_legacy_flags(self) -> None:
        """v0.1.0 flag surface must still be reachable under `scan`."""
        args = build_parser().parse_args([
            "scan",
            "--config", "foo.yaml",
            "--output", "both",
            "--severity", "critical",
            "--checks", "transport", "headers",
            "--fail-on", "warning",
            "--report", "llm",
            "--llm-backend", "claude",
        ])
        assert args.command == "scan"
        assert str(args.config) == "foo.yaml"
        assert args.output == "both"
        assert args.severity == "critical"
        assert args.checks == ["transport", "headers"]
        assert args.fail_on == "warning"
        assert args.report == "llm"
        assert args.llm_backend == "claude"

    def test_ui_defaults(self) -> None:
        args = build_parser().parse_args(["ui"])
        assert args.host == "127.0.0.1"
        assert args.port == 8765
        assert args.no_browser is False

    def test_ui_flag_overrides(self) -> None:
        args = build_parser().parse_args([
            "ui", "--host", "0.0.0.0", "--port", "9000", "--no-browser",
        ])
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.no_browser is True

    def test_init_spec_flag(self) -> None:
        args = build_parser().parse_args(["init", "--spec", "openapi.json"])
        assert args.spec == "openapi.json"

    def test_init_spec_default(self) -> None:
        args = build_parser().parse_args(["init"])
        assert args.spec is None


# ---------------------------------------------------------------------------
# Stubs — must exit cleanly with a clear message
# ---------------------------------------------------------------------------


class TestUIDispatch:
    """`sentinel ui` should hand off to the launcher with the parsed flags."""

    def test_ui_invokes_launcher_with_parsed_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("fastapi")
        called_with: dict[str, object] = {}

        def fake_launch(
            *, host: str, port: int, no_browser: bool, console: object
        ) -> int:
            called_with["host"] = host
            called_with["port"] = port
            called_with["no_browser"] = no_browser
            return 0

        from sentinel.ui import launcher

        monkeypatch.setattr(launcher, "launch", fake_launch)

        exit_code = main(["ui", "--port", "9000", "--no-browser"])

        assert exit_code == 0
        assert called_with["host"] == "127.0.0.1"
        assert called_with["port"] == 9000
        assert called_with["no_browser"] is True

    def test_ui_reports_clear_error_when_extras_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If sentinel.ui can't import (no FastAPI), the CLI must surface a
        clear `pip install` hint rather than a raw ImportError traceback."""
        import sys

        # Simulate the [ui] extras being uninstalled by forcing the launcher
        # import to fail mid-execution.
        monkeypatch.setitem(sys.modules, "sentinel.ui.launcher", None)

        exit_code = main(["ui"])
        captured = capsys.readouterr()

        assert exit_code != 0
        assert "Cannot start UI" in captured.out
        # The escaped [ui] mention must render (Rich-markup regression guard)
        assert "[ui]" in captured.out

    def test_ui_loads_dotenv_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """`sentinel ui` must load .env so users with secrets in .env see
        them in the env var picker — matching the CLI's scan behavior."""
        pytest.importorskip("fastapi")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "SENTINEL_UI6_DOTENV_TOKEN=loaded-from-dotenv\n",
            encoding="utf-8",
        )
        import os
        monkeypatch.delenv("SENTINEL_UI6_DOTENV_TOKEN", raising=False)

        captured_env: dict[str, str | None] = {}

        def fake_launch(*, host, port, no_browser, console):
            captured_env["value"] = os.environ.get("SENTINEL_UI6_DOTENV_TOKEN")
            return 0

        from sentinel.ui import launcher
        monkeypatch.setattr(launcher, "launch", fake_launch)

        exit_code = main(["ui"])
        assert exit_code == 0
        # If load_dotenv_file() ran before launch, the var should be set
        assert captured_env["value"] == "loaded-from-dotenv"


class TestRenderLLMNarrative:
    """Regression suite for the Phase 8 Rich-markup bug discovered in UI-1.

    The LLM panel was passing the raw narrative directly to Rich's Panel
    constructor, which parses `[bracket]` sequences as markup tags. Free-form
    LLM output can legitimately contain bracketed text (e.g. "see [section 3]"
    or "the [403] response code"), which Rich would silently drop. The fix
    escapes the narrative via `rich.markup.escape()` before rendering.
    """

    def test_brackets_render_literally(self) -> None:
        from io import StringIO

        from rich.console import Console

        from sentinel.cli import _render_llm_narrative

        buf = StringIO()
        console = Console(
            file=buf, force_terminal=False, highlight=False, width=120
        )
        _render_llm_narrative(
            console,
            "Reference [section 3] for the [WARNING] response.",
            "claude",
        )
        output = buf.getvalue()
        assert "[section 3]" in output
        assert "[WARNING]" in output

    def test_status_codes_in_brackets_survive(self) -> None:
        from io import StringIO

        from rich.console import Console

        from sentinel.cli import _render_llm_narrative

        buf = StringIO()
        console = Console(
            file=buf, force_terminal=False, highlight=False, width=120
        )
        _render_llm_narrative(
            console,
            "The API returned [403] on the protected endpoint.",
            "gemini",
        )
        assert "[403]" in buf.getvalue()

    def test_renders_title_and_backend_subtitle(self) -> None:
        from io import StringIO

        from rich.console import Console

        from sentinel.cli import _render_llm_narrative

        buf = StringIO()
        console = Console(
            file=buf, force_terminal=False, highlight=False, width=120
        )
        _render_llm_narrative(console, "Some narrative.", "openai")
        output = buf.getvalue()
        assert "LLM Security Analysis" in output
        assert "openai" in output


class TestInitStub:
    def test_init_stub_exits_zero_with_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["init"])
        captured = capsys.readouterr()
        assert exit_code == EXIT_OK
        assert "not yet implemented" in captured.out
