"""Tests for Codex rollout session parsing in promptvault.sync."""

from __future__ import annotations

from pathlib import Path

import json
import sqlite3

import pytest

from promptvault.search import (
    _build_conversation_lines,
    _build_prompt_lines,
    _source_badge,
)
from promptvault.sync import (
    _iso_to_ms,
    build_database,
    generate_markdown,
    main,
    parse_codex_sessions,
    parse_history,
)


class TestIsoToMs:
    def test_converts_iso_z_to_epoch_ms(self):
        assert _iso_to_ms("2026-07-09T09:53:20.000Z") == 1783590800000

    def test_handles_fractional_seconds(self):
        assert _iso_to_ms("2026-07-09T09:53:20.739Z") == 1783590800739

    def test_tolerates_nanosecond_precision(self):
        # Python 3.10 fromisoformat rejects 9-digit fractions; must not crash.
        assert _iso_to_ms("2026-07-09T09:53:20.123456789Z") == 1783590800123

    def test_tolerates_odd_fractional_digit_counts(self):
        # 1, 2, 4, 5-digit fractions are invalid for 3.10 fromisoformat.
        assert _iso_to_ms("2026-07-09T09:53:20.1Z") == 1783590800100

    def test_returns_none_on_garbage(self):
        assert _iso_to_ms("not-a-timestamp") is None


class TestParseCodexSessions:
    def test_extracts_user_prompts_only(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        texts = [p["display"] for p in sessions["codex-sess-1111"]]
        # noise (developer blob) excluded; consecutive duplicate collapsed
        assert texts == ["refactor the parser for codex", "now add a test for it"]

    def test_project_comes_from_cwd(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert sessions["codex-sess-1111"][0]["project"] == "/Users/test/project-c"

    def test_timestamps_are_epoch_ms(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert sessions["codex-sess-1111"][0]["timestamp"] == 1783590800000

    def test_source_tagged_codex(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert all(p["source"] == "codex" for p in sessions["codex-sess-1111"])

    def test_entry_shape_matches_claude(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        entry = sessions["codex-sess-1111"][0]
        assert set(entry) >= {"sessionId", "display", "timestamp", "project", "source"}
        assert entry["sessionId"] == "codex-sess-1111"

    def test_session_without_user_message_is_dropped(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert "codex-empty" not in sessions

    def test_file_without_session_meta_is_skipped(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert "codex-nometa" not in sessions
        assert all("orphan prompt" not in p["display"] for ps in sessions.values() for p in ps)

    def test_malformed_line_does_not_break_file(self, tmp_codex_sessions: Path):
        # File A had a trailing malformed line; its valid prompts still parse.
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert len(sessions["codex-sess-1111"]) == 2

    def test_second_file_parsed(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        assert [p["display"] for p in sessions["codex-sess-2222"]] == ["what does uv.lock do"]

    def test_missing_sessions_dir_returns_empty(self, tmp_path: Path):
        assert parse_codex_sessions(tmp_path / "nope") == {}

    def test_bad_event_timestamp_does_not_abort(self, tmp_path: Path):
        """A user_message with an unparseable timestamp keeps the prompt (falls back to meta ts)."""
        day = tmp_path / "sessions" / "2026" / "07" / "09"
        day.mkdir(parents=True)
        lines = [
            {
                "timestamp": "2026-07-09T09:53:16.000Z",
                "type": "session_meta",
                "payload": {"session_id": "codex-badts", "cwd": "/proj"},
            },
            {
                "timestamp": "garbage-timestamp",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "prompt with a bad ts"},
            },
        ]
        (day / "rollout-2026-07-09T09-53-16-codex-badts.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n"
        )
        sessions = parse_codex_sessions(tmp_path / "sessions")
        assert [p["display"] for p in sessions["codex-badts"]] == ["prompt with a bad ts"]
        # falls back to the session_meta timestamp
        assert sessions["codex-badts"][0]["timestamp"] == 1783590796000


class TestCodexDatabase:
    def test_source_column_is_codex(self, tmp_codex_sessions: Path, tmp_path: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
        db_path = tmp_path / "prompts.db"
        build_database(sessions, md_paths, db_path)

        conn = sqlite3.connect(str(db_path))
        sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM conversations")}
        assert sources == {"codex"}


class TestCodexMarkdown:
    def test_markdown_tagged_codex(self, tmp_codex_sessions: Path):
        sessions = parse_codex_sessions(tmp_codex_sessions)
        md = generate_markdown("codex-sess-1111", sessions["codex-sess-1111"], "name")
        assert "source: codex" in md
        assert "  - codex" in md
        assert "  - claude-code" not in md


def _write_claude_history(path: Path) -> None:
    entry = {
        "display": "explain claude side prompt",
        "pastedContents": {},
        "timestamp": 1700000000000,
        "project": "/Users/test/project-a",
        "sessionId": "claude-sess-aaaa",
    }
    path.write_text(json.dumps(entry) + "\n")


class TestSyncMainMerge:
    def _env(self, monkeypatch, tmp_path, *, history, codex):
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(tmp_path / "output"))
        monkeypatch.setenv("PROMPTVAULT_PROJECTS", str(tmp_path / "projects"))
        monkeypatch.setenv("PROMPTVAULT_CODEX_SESSIONS", str(codex))

    def _sources(self, tmp_path) -> set[str]:
        conn = sqlite3.connect(str(tmp_path / "output" / "prompts.db"))
        return {r[0] for r in conn.execute("SELECT DISTINCT source FROM conversations")}

    def test_merges_both_sources(self, tmp_path, monkeypatch, tmp_codex_sessions):
        history = tmp_path / "history.jsonl"
        _write_claude_history(history)
        self._env(monkeypatch, tmp_path, history=history, codex=tmp_codex_sessions)

        main(quiet=True)

        assert self._sources(tmp_path) == {"claude-code", "codex"}
        conn = sqlite3.connect(str(tmp_path / "output" / "prompts.db"))
        ids = {r[0] for r in conn.execute("SELECT session_id FROM conversations")}
        assert "claude-sess-aaaa" in ids and "codex-sess-1111" in ids

    def test_codex_only_builds_when_claude_history_absent(
        self, tmp_path, monkeypatch, tmp_codex_sessions
    ):
        self._env(monkeypatch, tmp_path, history=tmp_path / "nope.jsonl", codex=tmp_codex_sessions)

        main(quiet=True)

        assert self._sources(tmp_path) == {"codex"}

    def test_neither_source_exits_1(self, tmp_path, monkeypatch):
        self._env(
            monkeypatch, tmp_path, history=tmp_path / "nope.jsonl", codex=tmp_path / "no-codex"
        )

        with pytest.raises(SystemExit) as exc:
            main(quiet=False)
        assert exc.value.code == 1


@pytest.fixture
def mixed_db(tmp_history: Path, tmp_codex_sessions: Path, tmp_path: Path) -> Path:
    """A prompts.db built from both a Claude history and Codex rollout sessions."""
    sessions = parse_history(tmp_history)
    sessions.update(parse_codex_sessions(tmp_codex_sessions))
    md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
    db_path = tmp_path / "mixed.db"
    build_database(sessions, md_paths, db_path)
    return db_path


class TestSourceBadge:
    def test_badges_are_distinct_and_nonempty(self):
        assert _source_badge("codex") and _source_badge("claude-code")
        assert _source_badge("codex") != _source_badge("claude-code")

    def test_conversation_lines_carry_source_badge(self, mixed_db: Path):
        conn = sqlite3.connect(str(mixed_db))
        lines = _build_conversation_lines(conn)
        codex_line = next(line for line in lines if line.endswith("codex-sess-1111"))
        claude_line = next(line for line in lines if line.endswith("aaaa-1111-2222-3333"))
        assert _source_badge("codex") in codex_line
        assert _source_badge("claude-code") in claude_line

    def test_prompt_lines_carry_source_badge(self, mixed_db: Path):
        conn = sqlite3.connect(str(mixed_db))
        lines = _build_prompt_lines(conn)
        blob = "\n".join(lines)
        assert _source_badge("codex") in blob
        assert _source_badge("claude-code") in blob


class TestSourceFilter:
    def test_filter_codex_only(self, mixed_db: Path):
        conn = sqlite3.connect(str(mixed_db))
        lines = _build_conversation_lines(conn, source="codex")
        assert lines
        assert all(_source_badge("codex") in line for line in lines)
        assert all(_source_badge("claude-code") not in line for line in lines)

    def test_filter_claude_only(self, mixed_db: Path):
        conn = sqlite3.connect(str(mixed_db))
        lines = _build_conversation_lines(conn, source="claude-code")
        assert lines
        assert all(_source_badge("claude-code") in line for line in lines)
        assert all(_source_badge("codex") not in line for line in lines)

    def test_no_filter_returns_both(self, mixed_db: Path):
        conn = sqlite3.connect(str(mixed_db))
        blob = "\n".join(_build_conversation_lines(conn))
        assert _source_badge("codex") in blob and _source_badge("claude-code") in blob
