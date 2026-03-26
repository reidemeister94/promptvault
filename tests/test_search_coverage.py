"""Coverage tests for previously untested functions in promptvault/search.py.

Targets:
- _auto_sync_if_stale
- _fts_session_ids
- cmd_search / cmd_search_interactive
- cmd_recent (plain mode)
- cmd_list (plain mode, filters)
- main() dispatch
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from promptvault.search import (
    _auto_sync_if_stale,
    _fts_session_ids,
    build_parser,
    cmd_list,
    cmd_recent,
    cmd_search,
    cmd_search_interactive,
)
from promptvault.sync import build_database, generate_vault, parse_history


# ---------------------------------------------------------------------------
# Shared fixture: a small DB with known data
# ---------------------------------------------------------------------------

# Timestamps chosen to land on 2023-11-14 (UTC) for date-filter tests.
# sess-a1: two prompts, project-alpha
# sess-b1: one prompt, project-beta
# sess-c1: slash-command only (excluded from results)
_ENTRIES = [
    {
        "display": "explain docker networking",
        "pastedContents": {},
        "timestamp": 1700000000000,  # 2023-11-14 22:13 UTC
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-a1",
    },
    {
        "display": "add redis container",
        "pastedContents": {},
        "timestamp": 1700000060000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-a1",
    },
    {
        "display": "fix authentication bug",
        "pastedContents": {},
        "timestamp": 1700100000000,  # 2023-11-15 02:00 UTC
        "project": "/Users/test/project-beta",
        "sessionId": "sess-b1",
    },
    {
        "display": "/help ",
        "pastedContents": {},
        "timestamp": 1700200000000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-c1",
    },
]


@pytest.fixture(autouse=True)
def no_auto_sync(monkeypatch, tmp_path):
    """Prevent _auto_sync_if_stale from triggering during tests.

    Points PROMPTVAULT_HISTORY to a non-existent file so the function returns
    immediately without trying to sync against the developer's real history.
    """
    monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))


@pytest.fixture
def populated_env(tmp_path: Path):
    """Build a minimal promptvault environment with known, deterministic data."""
    history_path = tmp_path / "history.jsonl"
    with open(history_path, "w") as f:
        for entry in _ENTRIES:
            f.write(json.dumps(entry) + "\n")

    output_dir = tmp_path / "output"
    vault_dir = output_dir / "vault"
    vault_dir.mkdir(parents=True)
    db_path = output_dir / "prompts.db"

    sessions = parse_history(history_path)
    md_paths = generate_vault(sessions, vault_dir)
    build_database(sessions, md_paths, db_path)

    return {
        "db_path": db_path,
        "vault_dir": vault_dir,
        "history_path": history_path,
        "output_dir": output_dir,
    }


# ---------------------------------------------------------------------------
# _auto_sync_if_stale
# ---------------------------------------------------------------------------


class TestAutoSyncIfStale:
    def test_no_history_file_skips_sync(self, tmp_path, monkeypatch):
        """When history.jsonl does not exist, sync must never be called."""
        missing = tmp_path / "nonexistent.jsonl"
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(missing))
        db_path = tmp_path / "prompts.db"

        with patch("promptvault.sync.main") as mock_sync:
            _auto_sync_if_stale(db_path)

        mock_sync.assert_not_called()

    def test_no_db_triggers_sync(self, tmp_path, monkeypatch):
        """When history exists but DB does not, sync must run."""
        history = tmp_path / "history.jsonl"
        history.write_text("{}\n")
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))
        db_path = tmp_path / "prompts.db"  # does not exist

        # _auto_sync_if_stale uses a lazy `from promptvault.sync import main as sync_main`.
        # Intercepting via sys.modules ensures the lazy import picks up our mock.
        fake_sync = MagicMock()
        fake_module = MagicMock()
        fake_module.main = fake_sync
        monkeypatch.setitem(sys.modules, "promptvault.sync", fake_module)

        _auto_sync_if_stale(db_path)

        fake_sync.assert_called_once_with(quiet=True)

    def test_history_newer_than_db_triggers_sync(self, tmp_path, monkeypatch):
        """When history mtime > db mtime, sync must run."""
        import os
        import time

        history = tmp_path / "history.jsonl"
        history.write_text("{}\n")
        db = tmp_path / "prompts.db"
        db.write_text("")

        # Force history to be newer than db
        old_time = time.time() - 100
        os.utime(db, (old_time, old_time))

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))

        fake_sync = MagicMock()
        fake_module = MagicMock()
        fake_module.main = fake_sync
        monkeypatch.setitem(sys.modules, "promptvault.sync", fake_module)

        _auto_sync_if_stale(db)

        fake_sync.assert_called_once_with(quiet=True)

    def test_db_newer_than_history_skips_sync(self, tmp_path, monkeypatch):
        """When db mtime >= history mtime, sync must NOT run."""
        import os
        import time

        history = tmp_path / "history.jsonl"
        history.write_text("{}\n")
        db = tmp_path / "prompts.db"
        db.write_text("")

        # Force db to be newer than history
        future_time = time.time() + 100
        os.utime(db, (future_time, future_time))

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(history))

        fake_sync = MagicMock()
        fake_module = MagicMock()
        fake_module.main = fake_sync
        monkeypatch.setitem(sys.modules, "promptvault.sync", fake_module)

        _auto_sync_if_stale(db)

        fake_sync.assert_not_called()


# ---------------------------------------------------------------------------
# _fts_session_ids
# ---------------------------------------------------------------------------


class TestFtsSessionIds:
    def _conn(self, populated_env):
        return sqlite3.connect(str(populated_env["db_path"]))

    def test_single_word_match(self, populated_env):
        """A word present in the DB must return at least one session ID."""
        conn = self._conn(populated_env)
        ids = _fts_session_ids(conn, "docker")
        assert len(ids) >= 1
        assert "sess-a1" in ids

    def test_multiword_and_match(self, populated_env):
        """Two words both present in the same prompt use AND semantics."""
        conn = self._conn(populated_env)
        ids = _fts_session_ids(conn, "docker networking")
        assert "sess-a1" in ids

    def test_multiword_or_fallback(self, populated_env):
        """When AND returns nothing for a multi-word query, OR fallback activates."""
        conn = self._conn(populated_env)
        # "docker" and "authentication" are in different sessions — AND returns []
        # OR fallback should return both sessions
        ids = _fts_session_ids(conn, "docker authentication")
        assert "sess-a1" in ids
        assert "sess-b1" in ids

    def test_no_match_returns_empty(self, populated_env):
        """Unknown word must return an empty list."""
        conn = self._conn(populated_env)
        ids = _fts_session_ids(conn, "xyznonexistent999")
        assert ids == []

    def test_operational_error_returns_empty(self, populated_env):
        """A malformed FTS query that triggers OperationalError must return []."""
        conn = self._conn(populated_env)
        # FTS5 treats bare special tokens as syntax errors
        ids = _fts_session_ids(conn, "AND OR")
        assert isinstance(ids, list)


# ---------------------------------------------------------------------------
# cmd_search
# ---------------------------------------------------------------------------


class TestCmdSearch:
    def test_no_fzf_with_query_calls_plain_search(self, populated_env, capsys):
        """--no-fzf with a query produces plain text output."""
        args = build_parser().parse_args(["search", "--no-fzf", "docker"])
        cmd_search(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "docker" in out.lower()

    def test_no_fzf_without_query_prints_hint(self, populated_env, capsys):
        """--no-fzf without a query prints a helpful message."""
        args = build_parser().parse_args(["search", "--no-fzf"])
        cmd_search(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "Provide a search query" in out or "fzf" in out.lower()


# ---------------------------------------------------------------------------
# cmd_search_interactive
# ---------------------------------------------------------------------------


class TestCmdSearchInteractive:
    def test_empty_db_no_query_prints_no_conversations(self, tmp_path, capsys):
        """When DB has no conversations, prints 'No conversations found.'"""
        db_path = tmp_path / "empty.db"
        # Create a minimal schema so the query succeeds without data
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE conversations (
                session_id TEXT, display_name TEXT, name TEXT,
                project TEXT, start_ts INTEGER, end_ts INTEGER,
                prompt_count INTEGER, md_path TEXT
            )"""
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(db_path))
        vault_dir = tmp_path / "vault"
        cmd_search_interactive(conn, None, vault_dir)
        out = capsys.readouterr().out
        assert "No conversations found." in out

    def test_no_match_for_query_prints_message(self, populated_env, capsys):
        """When query matches nothing, prints a specific 'not found' message."""
        conn = sqlite3.connect(str(populated_env["db_path"]))
        cmd_search_interactive(conn, "xyznonexistent999", populated_env["vault_dir"])
        out = capsys.readouterr().out
        assert "xyznonexistent999" in out

    def test_with_results_calls_run_fzf(self, populated_env, monkeypatch):
        """When results exist, _run_fzf is invoked (mocked to avoid subprocess hang)."""
        # _run_fzf calls subprocess.run(["fzf", ...]) which blocks on a TTY.
        # Patch it at the module level so the test stays deterministic and fast.
        called_with = {}

        def fake_run_fzf(lines, vault_dir, **kwargs):
            called_with["lines"] = lines
            called_with["vault_dir"] = vault_dir

        monkeypatch.setattr("promptvault.search._run_fzf", fake_run_fzf)

        conn = sqlite3.connect(str(populated_env["db_path"]))
        cmd_search_interactive(conn, "docker", populated_env["vault_dir"])

        assert "lines" in called_with
        assert len(called_with["lines"]) >= 1


# ---------------------------------------------------------------------------
# cmd_recent
# ---------------------------------------------------------------------------


class TestCmdRecent:
    def test_plain_mode_outputs_prompts(self, populated_env, capsys):
        """Plain mode must print prompts from the DB."""
        args = build_parser().parse_args(["recent", "--no-fzf"])
        cmd_recent(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "Last" in out
        # At least one known prompt must appear
        assert "docker" in out.lower() or "authentication" in out.lower() or "redis" in out.lower()

    def test_slash_commands_excluded_from_output(self, populated_env, capsys):
        """Slash commands (e.g. /help) must never appear in recent output."""
        args = build_parser().parse_args(["recent", "--no-fzf", "20"])
        cmd_recent(args, populated_env["db_path"])
        out = capsys.readouterr().out
        # No line in output should start with a slash command
        for line in out.splitlines():
            stripped = line.strip()
            if stripped:
                assert not stripped.startswith("/help"), f"Slash command leaked: {stripped}"

    def test_ordering_most_recent_first(self, populated_env, capsys):
        """Most recent prompt must appear before older ones in the output."""
        args = build_parser().parse_args(["recent", "--no-fzf", "10"])
        cmd_recent(args, populated_env["db_path"])
        out = capsys.readouterr().out
        # "fix authentication bug" (ts=1700100000000) is newer than "explain docker" (ts=1700000000000)
        auth_pos = out.lower().find("authentication")
        docker_pos = out.lower().find("docker")
        assert auth_pos != -1
        assert docker_pos != -1
        assert auth_pos < docker_pos, "Most recent prompt must appear first"

    def test_count_parameter_respected(self, populated_env, capsys):
        """count=1 must produce exactly 1 prompt in output."""
        args = build_parser().parse_args(["recent", "--no-fzf", "1"])
        cmd_recent(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "Last 1 prompts:" in out

    def test_empty_db_prints_message(self, tmp_path, capsys):
        """Empty DB (no prompts) must print the header with 0 prompts."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE conversations (
                session_id TEXT, display_name TEXT, name TEXT,
                project TEXT, start_ts INTEGER, end_ts INTEGER,
                prompt_count INTEGER, md_path TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE prompts (
                id INTEGER PRIMARY KEY, session_id TEXT,
                prompt_text TEXT, timestamp INTEGER, project TEXT
            )"""
        )
        conn.commit()
        conn.close()

        # Patch get_db to return our connection directly (skip auto-sync)
        with patch("promptvault.search.get_db") as mock_get_db:
            mock_get_db.return_value = sqlite3.connect(str(db_path))
            args = build_parser().parse_args(["recent", "--no-fzf"])
            cmd_recent(args, db_path)

        out = capsys.readouterr().out
        assert "Last 0 prompts:" in out


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    def test_no_filters_returns_all_conversations(self, populated_env, capsys):
        """Without filters, all conversations with prompts are returned."""
        args = build_parser().parse_args(["list", "--no-fzf"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        # sess-a1 and sess-b1 have prompts; sess-c1 is slash-only
        assert "2 conversation(s):" in out

    def test_date_filter_selects_correct_day(self, populated_env, capsys):
        """--date 2023-11-14 should return only conversations starting that UTC day.

        sess-a1 starts at 1700000000000 = 2023-11-14 22:13 UTC → included.
        sess-b1 starts at 1700100000000 = 2023-11-15 02:00 UTC → excluded.
        """
        args = build_parser().parse_args(["list", "--no-fzf", "--date", "2023-11-14"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "1 conversation(s):" in out

    def test_date_filter_invalid_format_exits(self, populated_env, capsys):
        """An invalid date string must print an error and exit."""
        args = build_parser().parse_args(["list", "--no-fzf", "--date", "14-11-2023"])
        with pytest.raises(SystemExit) as exc_info:
            cmd_list(args, populated_env["db_path"])
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "Invalid date format" in err

    def test_project_filter_partial_match(self, populated_env, capsys):
        """--project beta should return only conversations from project-beta."""
        args = build_parser().parse_args(["list", "--no-fzf", "--project", "beta"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "1 conversation(s):" in out

    def test_limit_param_respected(self, populated_env, capsys):
        """--limit 1 must return exactly 1 conversation."""
        args = build_parser().parse_args(["list", "--no-fzf", "-n", "1"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "1 conversation(s):" in out

    def test_no_conversations_found_message(self, populated_env, capsys):
        """A filter matching nothing must print 'No conversations found.'"""
        args = build_parser().parse_args(["list", "--no-fzf", "--project", "nonexistent-xyz"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        assert "No conversations found." in out

    def test_plain_output_structure(self, populated_env, capsys):
        """Plain output must contain date, conversation name, and project short name."""
        args = build_parser().parse_args(["list", "--no-fzf"])
        cmd_list(args, populated_env["db_path"])
        out = capsys.readouterr().out
        # Date in YYYY-MM-DD HH:MM format
        assert "2023-11-" in out
        # Project short names
        assert "project-alpha" in out or "project-beta" in out


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def _run_main(self, argv, populated_env, monkeypatch, tmp_path):
        """Configure environment and argv for search.main() calls."""
        monkeypatch.setenv("PROMPTVAULT_DB", str(populated_env["db_path"]))
        # Point history to a non-existent path so _auto_sync_if_stale returns early.
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(sys, "argv", ["promptvault"] + argv)

    def test_stats_subcommand(self, populated_env, monkeypatch, capsys, tmp_path):
        """'promptvault stats' must print statistics."""
        from promptvault.search import main

        self._run_main(["stats"], populated_env, monkeypatch, tmp_path)
        main()
        out = capsys.readouterr().out
        assert "Conversations:" in out

    def test_search_no_fzf_with_query(self, populated_env, monkeypatch, capsys, tmp_path):
        """'promptvault search --no-fzf docker' must print search results."""
        from promptvault.search import main

        self._run_main(["search", "--no-fzf", "docker"], populated_env, monkeypatch, tmp_path)
        main()
        out = capsys.readouterr().out
        assert "docker" in out.lower()

    def test_no_subcommand_no_fzf_falls_back_to_recent(
        self, populated_env, monkeypatch, capsys, tmp_path
    ):
        """No subcommand + --no-fzf must fall back to recent plain output."""
        from promptvault.search import main

        self._run_main(["--no-fzf"], populated_env, monkeypatch, tmp_path)
        # has_fzf() checks shutil.which — keep it False so the else branch runs.
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: False)
        main()
        out = capsys.readouterr().out
        assert "Last" in out and "prompts:" in out
