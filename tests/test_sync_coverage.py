"""Coverage tests for untested functions in promptvault.sync."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from promptvault.sync import (
    _clean_for_title,
    format_duration,
    generate_markdown,
    make_display_name,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    """Construct a UTC datetime for readable test setup."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _prompt(text: str, ts_ms: int = 1_700_000_000_000, project: str = "/proj") -> dict:
    """Build a minimal prompt dict matching the shape parse_history produces."""
    return {"display": text, "timestamp": ts_ms, "project": project}


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "<1 min"),
            (30, "<1 min"),
            (59, "<1 min"),
            (60, "1 min"),
            (90, "1 min"),  # 1.5 minutes → 1 min (truncated)
            (30 * 60, "30 min"),
            (59 * 60 + 59, "59 min"),
            (60 * 60, "1h 0m"),
            (90 * 60, "1h 30m"),
            (125 * 60, "2h 5m"),
        ],
    )
    def test_duration_boundaries(self, seconds: int, expected: str):
        start = _dt(2024, 1, 1)
        end = _dt(
            2024, 1, 1, second=seconds % 60, minute=(seconds // 60) % 60, hour=seconds // 3600
        )
        # Recompute using timedelta to avoid manual hour/min/sec arithmetic mistakes
        from datetime import timedelta

        end = start + timedelta(seconds=seconds)
        assert format_duration(start, end) == expected


# ---------------------------------------------------------------------------
# _clean_for_title
# ---------------------------------------------------------------------------


class TestCleanForTitle:
    def test_image_marker_removed(self):
        result = _clean_for_title("Look at [Image #1] and tell me")
        assert "[Image #1]" not in result
        assert "Look at" in result
        assert "and tell me" in result

    def test_image_marker_with_suffix_removed(self):
        # Markers can carry extra text like "Image #3 (600x400)"
        result = _clean_for_title("[Image #3 (600x400)] describe this")
        assert "[Image" not in result
        assert "describe this" in result

    def test_pasted_text_marker_removed(self):
        result = _clean_for_title("[Pasted text #2 +10 lines] analyze this")
        assert "[Pasted text" not in result
        assert "analyze this" in result

    def test_cd_env_prefix_removed(self):
        # Typical debug paste: "cd /some/dir ; /usr/bin/env python script.py"
        result = _clean_for_title("cd /foo/bar ; /usr/bin/env python3 rest of message")
        assert result == "rest of message"

    def test_multiple_markers_removed(self):
        text = "[Image #1] see [Pasted text #1 +2 lines] also"
        result = _clean_for_title(text)
        assert "[Image" not in result
        assert "[Pasted text" not in result
        assert "see" in result
        assert "also" in result

    def test_clean_text_unchanged(self):
        text = "Explain how to write unit tests"
        assert _clean_for_title(text) == text

    def test_whitespace_collapsed(self):
        result = _clean_for_title("too   many    spaces")
        assert result == "too many spaces"

    def test_only_markers_becomes_empty(self):
        result = _clean_for_title("[Image #1][Pasted text #1 +1 lines]")
        assert result == ""


# ---------------------------------------------------------------------------
# make_display_name
# ---------------------------------------------------------------------------


class TestMakeDisplayName:
    def test_summary_takes_precedence(self):
        prompts = [_prompt("some prompt")]
        result = make_display_name(prompts, "sid", summary="My Custom Summary")
        assert result == "My Custom Summary"

    def test_first_real_prompt_capitalised(self):
        prompts = [_prompt("explain how pytest works")]
        result = make_display_name(prompts, "sid")
        assert result == "Explain how pytest works"

    def test_slash_commands_skipped(self):
        prompts = [
            _prompt("/help"),
            _prompt("/compact"),
        ]
        result = make_display_name(prompts, "sid")
        assert result == "(no text prompts)"

    def test_very_short_prompts_skipped(self):
        # Prompts of exactly 2 chars (len <= 2) are ignored; "ok" is 2 chars
        prompts = [_prompt("ok"), _prompt("hi")]
        result = make_display_name(prompts, "sid")
        assert result == "(no text prompts)"

    def test_prompt_after_short_ones_used(self):
        # "ok" (2 chars) and "hi" (2 chars) are both skipped; the third is used
        prompts = [
            _prompt("ok"),
            _prompt("hi"),
            _prompt("Fix the login bug"),
        ]
        result = make_display_name(prompts, "sid")
        assert result == "Fix the login bug"

    def test_long_prompt_truncated_at_80(self):
        long_text = "x" * 90
        prompts = [_prompt(long_text)]
        result = make_display_name(prompts, "sid")
        # Title capped at 80 chars + "..."
        assert result == "X" + "x" * 79 + "..."
        assert len(result) == 83

    def test_prompt_exactly_80_chars_not_truncated(self):
        text = "a" * 80
        prompts = [_prompt(text)]
        result = make_display_name(prompts, "sid")
        assert not result.endswith("...")
        assert len(result) == 80

    def test_only_marker_prompt_skipped(self):
        # After cleaning, fewer than 3 chars remain → skip
        prompts = [
            _prompt("[Image #1]"),
            _prompt("real question here"),
        ]
        result = make_display_name(prompts, "sid")
        assert result == "Real question here"

    def test_empty_prompts_list(self):
        result = make_display_name([], "sid")
        assert result == "(no text prompts)"


# ---------------------------------------------------------------------------
# generate_markdown
# ---------------------------------------------------------------------------


class TestGenerateMarkdown:
    def _make_prompts(self) -> list[dict]:
        return [
            _prompt("explain fixtures", ts_ms=1_700_000_000_000),
            _prompt("add type hints", ts_ms=1_700_000_060_000),
        ]

    def test_frontmatter_contains_session_id(self):
        md = generate_markdown("sess-abc", self._make_prompts(), "test-name")
        assert "session_id: sess-abc" in md

    def test_frontmatter_contains_project(self):
        prompts = [_prompt("hello", project="/my/project")]
        md = generate_markdown("s1", prompts, "n")
        assert "project: /my/project" in md

    def test_frontmatter_contains_started_and_ended(self):
        md = generate_markdown("s1", self._make_prompts(), "n")
        assert "started:" in md
        assert "ended:" in md

    def test_frontmatter_prompt_count_excludes_slash_commands(self):
        prompts = [
            _prompt("/help", ts_ms=1_700_000_000_000),
            _prompt("real question", ts_ms=1_700_000_060_000),
        ]
        md = generate_markdown("s1", prompts, "n")
        assert "prompt_count: 1" in md

    def test_frontmatter_tags_present(self):
        md = generate_markdown("s1", self._make_prompts(), "n")
        assert "tags:" in md
        assert "claude-code" in md
        assert "promptvault" in md

    def test_prompt_headings_present(self):
        md = generate_markdown("s1", self._make_prompts(), "n")
        assert "## Prompt 1" in md
        assert "## Prompt 2" in md

    def test_prompt_text_included(self):
        md = generate_markdown("s1", self._make_prompts(), "n")
        assert "explain fixtures" in md
        assert "add type hints" in md

    def test_slash_command_filtered_from_body(self):
        prompts = [
            _prompt("/compact", ts_ms=1_700_000_000_000),
            _prompt("real work", ts_ms=1_700_000_060_000),
        ]
        md = generate_markdown("s1", prompts, "n")
        assert "/compact" not in md
        assert "real work" in md

    def test_trailing_whitespace_stripped(self):
        prompts = [_prompt("line with spaces   \n  \nclean line", ts_ms=1_700_000_000_000)]
        md = generate_markdown("s1", prompts, "n")
        # No line should end with whitespace (spaces or tabs)
        for line in md.splitlines():
            assert line == line.rstrip(), f"Trailing whitespace found: {line!r}"

    def test_title_capitalised(self):
        prompts = [_prompt("describe the architecture", ts_ms=1_700_000_000_000)]
        md = generate_markdown("s1", prompts, "n")
        assert "# Describe the architecture" in md


# ---------------------------------------------------------------------------
# main() — integration tests
# ---------------------------------------------------------------------------


class TestMain:
    """Integration tests for the sync entry point.

    We redirect PROMPTVAULT_HISTORY, PROMPTVAULT_OUTPUT, and
    PROMPTVAULT_PROJECTS via env vars so the real ~/.claude paths are
    never touched.
    """

    def _write_history(self, path: Path, entries: list[dict]) -> None:
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _minimal_entry(self, session_id: str = "sess-0001", ts: int = 1_700_000_000_000) -> dict:
        return {
            "display": "explain how async works",
            "pastedContents": {},
            "timestamp": ts,
            "project": "/proj",
            "sessionId": session_id,
        }

    def test_normal_run_creates_vault_db_index(self, tmp_path: Path, monkeypatch):
        history = tmp_path / "history.jsonl"
        output = tmp_path / "output"
        self._write_history(history, [self._minimal_entry()])

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(output))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))

        main(quiet=True)

        vault_dir = output / "vault"
        assert vault_dir.is_dir()
        assert (output / "prompts.db").exists()
        assert (vault_dir / "_index.md").exists()

    def test_missing_history_exits_with_code_1(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no_such_file.jsonl"))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(tmp_path / "output"))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))

        with pytest.raises(SystemExit) as exc_info:
            main(quiet=False)

        assert exc_info.value.code == 1

    def test_missing_history_quiet_returns_without_exit(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no_such_file.jsonl"))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(tmp_path / "output"))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))

        # quiet=True must return cleanly instead of sys.exit
        result = main(quiet=True)
        assert result is None

    def test_quiet_mode_no_stdout(self, tmp_path: Path, monkeypatch, capsys):
        history = tmp_path / "history.jsonl"
        output = tmp_path / "output"
        self._write_history(history, [self._minimal_entry()])

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(output))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))

        main(quiet=True)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_idempotent_second_run_same_db_count(self, tmp_path: Path, monkeypatch):
        history = tmp_path / "history.jsonl"
        output = tmp_path / "output"
        self._write_history(
            history,
            [
                self._minimal_entry("sess-0001", 1_700_000_000_000),
                self._minimal_entry("sess-0002", 1_700_100_000_000),
            ],
        )

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(output))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))

        main(quiet=True)
        main(quiet=True)

        db_path = output / "prompts.db"
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.close()
        assert count == 2
