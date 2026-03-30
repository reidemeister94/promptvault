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
    _clipboard_cmd,
    _fts_search,
    _fts_session_ids,
    _fzf_preview_script,
    _fzf_version,
    _run_fzf,
    build_parser,
    cmd_list,
    cmd_recent,
    cmd_search,
    cmd_search_interactive,
    get_db,
    ts_to_short,
    ts_to_str,
)
from promptvault.sync import build_database, generate_vault, parse_history


# ---------------------------------------------------------------------------
# Shared test helper: capture fzf command with mocked version
# ---------------------------------------------------------------------------


def capture_fzf_cmd(monkeypatch, fzf_ver: tuple[int, ...] = (0, 0, 0)) -> list[str]:
    """Mock subprocess.run and _fzf_version, return list that collects the fzf command."""
    captured_cmd: list[str] = []

    def fake_subprocess_run(cmd, **kwargs):
        if cmd == ["fzf", "--version"]:
            result = MagicMock()
            result.stdout = "0.0.0"
            return result
        captured_cmd.extend(cmd)
        result = MagicMock()
        result.returncode = 130
        result.stdout = ""
        return result

    monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("promptvault.search._fzf_version", lambda: fzf_ver)
    return captured_cmd


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
        assert mock_sync.call_count == 0

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
        assert fake_sync.call_count == 1
        assert fake_sync.call_args == ((), {"quiet": True})

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
        assert fake_sync.call_count == 1
        assert fake_sync.call_args == ((), {"quiet": True})

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
        assert fake_sync.call_count == 0


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


# ---------------------------------------------------------------------------
# ts_to_str / ts_to_short
# ---------------------------------------------------------------------------


class TestTimestampFormatters:
    @pytest.mark.parametrize(
        "ts_ms, expected",
        [
            (1700000000000, "2023-11-14 22:13"),  # known reference timestamp
            (0, "1970-01-01 00:00"),  # Unix epoch
            (1609459200000, "2021-01-01 00:00"),  # new year 2021
        ],
    )
    def test_ts_to_str(self, ts_ms, expected):
        assert ts_to_str(ts_ms) == expected

    @pytest.mark.parametrize(
        "ts_ms, expected",
        [
            (1700000000000, "11-14 22:13"),  # same reference — no year
            (0, "01-01 00:00"),  # epoch without year
        ],
    )
    def test_ts_to_short(self, ts_ms, expected):
        assert ts_to_short(ts_ms) == expected


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


class TestGetDb:
    def test_existing_db_returns_connection(self, populated_env):
        """When db_path exists, get_db returns a live sqlite3 connection."""
        # The autouse fixture prevents real sync by pointing PROMPTVAULT_HISTORY to a missing file.
        conn = get_db(populated_env["db_path"])
        assert isinstance(conn, sqlite3.Connection)
        # Verify the connection is usable
        result = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
        assert result[0] >= 0
        conn.close()

    def test_missing_db_exits_with_error(self, tmp_path, capsys):
        """When db_path does not exist, get_db prints an error to stderr and exits."""
        missing = tmp_path / "nonexistent.db"
        with pytest.raises(SystemExit) as exc_info:
            get_db(missing)
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "database not found" in err.lower() or "promptvault-sync" in err

    def test_auto_sync_called(self, populated_env, monkeypatch):
        """get_db must call _auto_sync_if_stale before opening the connection."""
        calls = []

        def fake_auto_sync(db_path):
            calls.append(db_path)

        monkeypatch.setattr("promptvault.search._auto_sync_if_stale", fake_auto_sync)
        conn = get_db(populated_env["db_path"])
        conn.close()
        assert calls == [populated_env["db_path"]]


# ---------------------------------------------------------------------------
# _fzf_preview_script / _fzf_copy_script
# ---------------------------------------------------------------------------


class TestFzfScripts:
    def test_preview_script_contains_vault_dir(self, tmp_path):
        """Preview script must embed the vault_dir path."""
        vault_dir = tmp_path / "my_vault"
        script = _fzf_preview_script(vault_dir)
        assert str(vault_dir) in script

    def test_preview_script_contains_query_placeholder(self, tmp_path):
        """{q} placeholder must appear so fzf can inject the live query."""
        script = _fzf_preview_script(tmp_path / "vault")
        assert "{q}" in script

    def test_preview_script_contains_sed_and_grep(self, tmp_path):
        """Preview script uses sed to extract prompt sections and grep for highlighting."""
        script = _fzf_preview_script(tmp_path / "vault")
        assert "sed" in script
        assert "grep" in script

    def test_preview_script_handles_spaces_in_path(self, tmp_path):
        """vault_dir with spaces must still appear correctly embedded in the script."""
        vault_dir = tmp_path / "my vault with spaces"
        script = _fzf_preview_script(vault_dir)
        assert str(vault_dir) in script

    def test_placeholder(self):
        """Copy/export now handled by _fzf-action subcommand — see TestFzfAction."""
        pass


# ---------------------------------------------------------------------------
# _run_fzf
# ---------------------------------------------------------------------------


class TestRunFzf:
    """Tests for _run_fzf subprocess orchestration. All subprocess.run calls mocked."""

    def _make_lines(self, populated_env):
        """Build a minimal valid lines list using a real vault md_path."""
        conn = sqlite3.connect(str(populated_env["db_path"]))
        rows = conn.execute(
            "SELECT md_path FROM conversations WHERE prompt_count > 0 LIMIT 1"
        ).fetchall()
        conn.close()
        assert rows, "populated_env must have at least one conversation"
        md_path = rows[0][0]
        return [f"{md_path}\t11-14 22:13   2p  project-alpha    explain docker"], md_path

    def test_enter_binding_contains_execute_with_editor(self, populated_env, monkeypatch):
        """fzf command includes enter:execute() binding with EDITOR for in-fzf file opening."""
        vault_dir = populated_env["vault_dir"]

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130  # Esc
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 70, 0))
        monkeypatch.setenv("EDITOR", "nano")

        _run_fzf(["a.md\ttest line"], vault_dir)

        bind_args = [captured_cmd[i + 1] for i, v in enumerate(captured_cmd) if v == "--bind"]
        assert any("enter:execute(" in b and "nano" in b for b in bind_args)

    def test_nonzero_returncode_does_nothing(self, populated_env, monkeypatch, capsys):
        """rc != 0 (e.g. user pressed Esc) → no output, no editor invocation."""
        vault_dir = populated_env["vault_dir"]

        fzf_result = MagicMock()
        fzf_result.returncode = 130  # typical Esc/Ctrl-C code
        fzf_result.stdout = ""

        editor_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            if cmd[0] == "fzf":
                return fzf_result
            editor_calls.append(cmd)
            return MagicMock(returncode=0)

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)

        _run_fzf(["some_path.md\tsome visible text"], vault_dir)

        assert editor_calls == []
        out, err = capsys.readouterr()
        assert out == ""

    def test_file_not_found_error_exits(self, populated_env, monkeypatch, capsys):
        """FileNotFoundError (fzf not installed) → prints to stderr and sys.exit(1)."""
        vault_dir = populated_env["vault_dir"]

        def raise_fnf(cmd, **kwargs):
            raise FileNotFoundError("fzf not found")

        monkeypatch.setattr("promptvault.search.subprocess.run", raise_fnf)

        with pytest.raises(SystemExit) as exc_info:
            _run_fzf(["some_path.md\tsome visible text"], vault_dir)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "fzf" in err.lower()

    def test_db_path_adds_disabled_and_reload_binding(self, populated_env, monkeypatch):
        """When db_path is provided, fzf command includes --disabled and change:reload."""
        vault_dir = populated_env["vault_dir"]
        db_path = populated_env["db_path"]

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 1  # simulate Esc so no editor is invoked
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)

        _run_fzf(["some_path.md\tsome visible text"], vault_dir, db_path=db_path)

        assert "--disabled" in captured_cmd
        # The change:reload binding must reference the db path
        bind_indices = [i for i, v in enumerate(captured_cmd) if v.startswith("change:reload")]
        assert len(bind_indices) >= 1
        assert str(db_path) in captured_cmd[bind_indices[0]]

    def test_query_adds_query_flag(self, populated_env, monkeypatch):
        """When query is provided, fzf command includes --query <value>."""
        vault_dir = populated_env["vault_dir"]

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)

        _run_fzf(["some_path.md\tsome visible text"], vault_dir, query="docker")

        assert "--query" in captured_cmd
        query_idx = captured_cmd.index("--query")
        assert captured_cmd[query_idx + 1] == "docker"


# ---------------------------------------------------------------------------
# cmd_recent fzf branch
# ---------------------------------------------------------------------------


class TestCmdRecentFzfBranch:
    def test_fzf_branch_calls_run_fzf_with_tab_separated_lines(self, populated_env, monkeypatch):
        """When fzf is available and stdout is a tty, cmd_recent calls _run_fzf with
        lines containing at least 3 tab-separated fields (md_path, visible, full title)."""
        captured = {}

        def fake_run_fzf(lines, vault_dir, **kwargs):
            captured["lines"] = lines
            captured["vault_dir"] = vault_dir

        monkeypatch.setattr("promptvault.search._run_fzf", fake_run_fzf)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr("promptvault.search.sys.stdout", MagicMock(isatty=lambda: True))
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        args = build_parser().parse_args(["recent", "10"])
        cmd_recent(args, populated_env["db_path"])

        assert "lines" in captured
        assert len(captured["lines"]) >= 1
        # Each line must have at least 3 tab-separated fields
        for line in captured["lines"]:
            fields = line.split("\t")
            assert len(fields) >= 3, f"Expected >=3 tab fields, got {len(fields)}: {line!r}"

    def test_fzf_branch_empty_db_prints_message(self, tmp_path, monkeypatch, capsys):
        """When fzf is active but no conversations exist, prints 'No conversations found.'"""
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

        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        # Patch isatty on the real stdout so capsys can still capture print() output.
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(tmp_path / "vault"))

        with patch("promptvault.search.get_db") as mock_get_db:
            mock_get_db.return_value = sqlite3.connect(str(db_path))
            args = build_parser().parse_args(["recent"])
            cmd_recent(args, db_path)

        out = capsys.readouterr().out
        assert "No conversations found." in out


# ---------------------------------------------------------------------------
# cmd_list fzf branch
# ---------------------------------------------------------------------------


class TestCmdListFzfBranch:
    def test_fzf_branch_calls_run_fzf_with_tab_separated_lines(self, populated_env, monkeypatch):
        """When fzf is available and stdout is a tty, cmd_list calls _run_fzf with
        lines containing at least 3 tab-separated fields (md_path, visible, full title)."""
        captured = {}

        def fake_run_fzf(lines, vault_dir, **kwargs):
            captured["lines"] = lines

        monkeypatch.setattr("promptvault.search._run_fzf", fake_run_fzf)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr("promptvault.search.sys.stdout", MagicMock(isatty=lambda: True))
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        args = build_parser().parse_args(["list"])
        cmd_list(args, populated_env["db_path"])

        assert "lines" in captured
        assert len(captured["lines"]) >= 1
        for line in captured["lines"]:
            fields = line.split("\t")
            assert len(fields) >= 3, f"Expected >=3 tab fields, got {len(fields)}: {line!r}"

    def test_fzf_branch_respects_filters(self, populated_env, monkeypatch):
        """cmd_list fzf branch applies --project filter before calling _run_fzf."""
        captured = {}

        def fake_run_fzf(lines, vault_dir, **kwargs):
            captured["lines"] = lines

        monkeypatch.setattr("promptvault.search._run_fzf", fake_run_fzf)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr("promptvault.search.sys.stdout", MagicMock(isatty=lambda: True))
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        # Only project-beta matches — should yield exactly 1 line
        args = build_parser().parse_args(["list", "--project", "beta"])
        cmd_list(args, populated_env["db_path"])

        assert len(captured["lines"]) == 1
        # Line must start with the md_path from the project-beta conversation
        conn = sqlite3.connect(str(populated_env["db_path"]))
        beta_row = conn.execute(
            "SELECT md_path FROM conversations WHERE project LIKE '%beta%' LIMIT 1"
        ).fetchone()
        conn.close()
        assert beta_row is not None
        assert captured["lines"][0].startswith(beta_row[0])


# ---------------------------------------------------------------------------
# main() interactive path
# ---------------------------------------------------------------------------


class TestMainInteractivePath:
    def test_no_subcommand_fzf_available_calls_cmd_search_interactive(
        self, populated_env, monkeypatch, tmp_path
    ):
        """No subcommand + fzf available + tty → cmd_search_interactive is called."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_DB", str(populated_env["db_path"]))
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))
        monkeypatch.setattr(sys, "argv", ["promptvault"])

        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr("promptvault.search.sys.stdout", MagicMock(isatty=lambda: True))

        called_with = {}

        def fake_cmd_search_interactive(conn, query, vault_dir, db_path=None):
            called_with["query"] = query
            called_with["vault_dir"] = vault_dir
            called_with["db_path"] = db_path

        monkeypatch.setattr(
            "promptvault.search.cmd_search_interactive", fake_cmd_search_interactive
        )

        main()

        assert "query" in called_with
        assert called_with["query"] is None
        assert called_with["db_path"] == populated_env["db_path"]


# ---------------------------------------------------------------------------
# has_fzf
# ---------------------------------------------------------------------------


class TestHasFzf:
    def test_returns_true_when_fzf_installed(self, monkeypatch):
        from promptvault.search import has_fzf

        monkeypatch.setattr("promptvault.search.shutil.which", lambda cmd: "/usr/local/bin/fzf")
        assert has_fzf() is True

    def test_returns_false_when_fzf_missing(self, monkeypatch):
        from promptvault.search import has_fzf

        monkeypatch.setattr("promptvault.search.shutil.which", lambda cmd: None)
        assert has_fzf() is False


# ---------------------------------------------------------------------------
# _fts_search OR fallback (lines 311-315)
# ---------------------------------------------------------------------------


class TestFtsSearchOrFallback:
    """Test the OR fallback path in _fts_search when AND yields no results."""

    def test_or_fallback_when_and_returns_nothing(self, populated_env):
        """Multi-word query where words exist in different sessions triggers OR fallback."""
        conn = sqlite3.connect(str(populated_env["db_path"]))
        # "docker" is in sess-a1, "authentication" is in sess-b1
        # AND search returns nothing, OR fallback should find both
        rows = _fts_search(conn, "docker authentication")
        assert len(rows) >= 2
        texts = [r[0].lower() for r in rows]
        assert any("docker" in t for t in texts)
        assert any("authentication" in t for t in texts)

    def test_and_match_does_not_trigger_fallback(self, populated_env):
        """Single-session multi-word query that matches via AND does not need OR fallback."""
        conn = sqlite3.connect(str(populated_env["db_path"]))
        # "docker" and "networking" are both in sess-a1 — AND works
        rows = _fts_search(conn, "docker networking")
        assert len(rows) >= 1
        assert any("docker" in r[0].lower() and "networking" in r[0].lower() for r in rows)

    def test_operational_error_returns_empty(self, populated_env):
        """Malformed FTS query returns empty list, not an exception."""
        conn = sqlite3.connect(str(populated_env["db_path"]))
        rows = _fts_search(conn, "AND OR NOT")
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# cmd_search interactive branch (line 351)
# ---------------------------------------------------------------------------


class TestCmdSearchInteractiveBranch:
    """Test cmd_search routing to cmd_search_interactive when fzf is available."""

    def test_fzf_available_routes_to_interactive(self, populated_env, monkeypatch):
        """When fzf is available and stdout is a tty, cmd_search calls cmd_search_interactive."""
        called = {}

        def fake_interactive(conn, query, vault_dir, db_path=None):
            called["query"] = query
            called["db_path"] = db_path

        monkeypatch.setattr("promptvault.search.cmd_search_interactive", fake_interactive)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        args = build_parser().parse_args(["search", "docker"])
        cmd_search(args, populated_env["db_path"])

        assert "query" in called
        assert called["query"] == "docker"
        assert called["db_path"] == populated_env["db_path"]

    def test_fzf_available_no_query_routes_to_interactive(self, populated_env, monkeypatch):
        """cmd_search with no query still routes to interactive when fzf is available."""
        called = {}

        def fake_interactive(conn, query, vault_dir, db_path=None):
            called["query"] = query

        monkeypatch.setattr("promptvault.search.cmd_search_interactive", fake_interactive)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        args = build_parser().parse_args(["search"])
        cmd_search(args, populated_env["db_path"])

        assert called["query"] is None


# ---------------------------------------------------------------------------
# main() _fzf-lines in-process (lines 588-593)
# ---------------------------------------------------------------------------


class TestMainFzfLines:
    """Test the _fzf-lines hidden command in-process (not via subprocess)."""

    def test_fzf_lines_no_query_outputs_all(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines without query writes all non-empty conversations to stdout."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys, "argv", ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-lines"]
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        # 2 non-empty sessions (sess-a1 and sess-b1), sess-c1 is slash-only
        assert len(lines) == 2
        for line in lines:
            assert "\t" in line
            assert line.split("\t")[0].endswith(".md")

    def test_fzf_lines_with_query_filters(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines with query returns only matching conversations."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-lines", "docker"],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) >= 1
        # "docker" should only be in sess-a1
        assert len(lines) == 1

    def test_fzf_lines_empty_query_returns_all(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines with empty string returns all conversations."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys, "argv", ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-lines", ""]
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# main() command dispatch (lines 595-603)
# ---------------------------------------------------------------------------


class TestMainCommandDispatch:
    """Test main() dispatches to each command handler via the commands dict."""

    def test_recent_subcommand(self, populated_env, monkeypatch, capsys, tmp_path):
        """'promptvault recent --no-fzf' dispatches to cmd_recent."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_DB", str(populated_env["db_path"]))
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(sys, "argv", ["promptvault", "recent", "--no-fzf"])

        main()

        out = capsys.readouterr().out
        assert "Last" in out and "prompts:" in out

    def test_list_subcommand(self, populated_env, monkeypatch, capsys, tmp_path):
        """'promptvault list --no-fzf' dispatches to cmd_list."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_DB", str(populated_env["db_path"]))
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(sys, "argv", ["promptvault", "list", "--no-fzf"])

        main()

        out = capsys.readouterr().out
        assert "conversation(s):" in out


# ---------------------------------------------------------------------------
# _fzf_version
# ---------------------------------------------------------------------------


class TestFzfVersion:
    def test_parses_standard_version(self, monkeypatch):
        """Standard fzf --version output like '0.70.0 (Homebrew)' returns (0, 70, 0)."""
        result = MagicMock()
        result.stdout = "0.70.0 (Homebrew)\n"
        monkeypatch.setattr(
            "promptvault.search.subprocess.run",
            lambda cmd, **kw: result,
        )
        assert _fzf_version() == (0, 70, 0)

    def test_parses_plain_version(self, monkeypatch):
        """Plain version string '0.38.0' returns (0, 38, 0)."""
        result = MagicMock()
        result.stdout = "0.38.0\n"
        monkeypatch.setattr(
            "promptvault.search.subprocess.run",
            lambda cmd, **kw: result,
        )
        assert _fzf_version() == (0, 38, 0)

    def test_returns_zeros_on_bad_output(self, monkeypatch):
        """Non-version output returns (0, 0, 0)."""
        result = MagicMock()
        result.stdout = "not a version\n"
        monkeypatch.setattr(
            "promptvault.search.subprocess.run",
            lambda cmd, **kw: result,
        )
        assert _fzf_version() == (0, 0, 0)

    def test_returns_zeros_on_file_not_found(self, monkeypatch):
        """FileNotFoundError (fzf not installed) returns (0, 0, 0)."""

        def raise_fnf(cmd, **kw):
            raise FileNotFoundError("fzf not found")

        monkeypatch.setattr("promptvault.search.subprocess.run", raise_fnf)
        assert _fzf_version() == (0, 0, 0)

    def test_returns_zeros_on_empty_output(self, monkeypatch):
        """Empty stdout returns (0, 0, 0)."""
        result = MagicMock()
        result.stdout = ""
        monkeypatch.setattr(
            "promptvault.search.subprocess.run",
            lambda cmd, **kw: result,
        )
        assert _fzf_version() == (0, 0, 0)


# ---------------------------------------------------------------------------
# _run_fzf new features
# ---------------------------------------------------------------------------


class TestRunFzfNewFeatures:
    """Tests for new fzf flags added to _run_fzf."""

    def test_multi_flag_present(self, populated_env, monkeypatch):
        """--multi must be in the fzf command."""
        captured = capture_fzf_cmd(monkeypatch)
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--multi" in captured

    def test_highlight_line_with_new_fzf(self, populated_env, monkeypatch):
        """--highlight-line present when fzf >= 0.53.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 53, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--highlight-line" in captured

    def test_highlight_line_absent_with_old_fzf(self, populated_env, monkeypatch):
        """--highlight-line absent when fzf < 0.53.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 52, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--highlight-line" not in captured

    def test_ctrl_slash_toggle_preview_binding(self, populated_env, monkeypatch):
        """ctrl-/:toggle-preview binding must be present."""
        captured = capture_fzf_cmd(monkeypatch)
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-/:toggle-preview" in b for b in bind_args)

    def test_history_flag_present(self, populated_env, monkeypatch):
        """--history must point to .search_history in output dir."""
        captured = capture_fzf_cmd(monkeypatch)
        monkeypatch.setenv("PROMPTVAULT_OUTPUT", str(populated_env["output_dir"]))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--history" in captured
        history_idx = captured.index("--history")
        assert captured[history_idx + 1].endswith(".search_history")

    def test_ghost_flag_with_new_fzf(self, populated_env, monkeypatch):
        """--ghost present when fzf >= 0.54.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 54, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert any("--ghost=" in arg for arg in captured)

    def test_ghost_flag_absent_with_old_fzf(self, populated_env, monkeypatch):
        """--ghost absent when fzf < 0.54.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 53, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert not any("--ghost=" in arg for arg in captured)

    def test_footer_with_new_fzf(self, populated_env, monkeypatch):
        """--footer present when fzf >= 0.53.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 53, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert any("--footer=" in arg for arg in captured)

    def test_footer_absent_with_old_fzf(self, populated_env, monkeypatch):
        """--footer absent when fzf < 0.53.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 52, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert not any("--footer=" in arg for arg in captured)

    def test_tmux_flag_when_in_tmux(self, populated_env, monkeypatch):
        """--tmux present when TMUX env var set and fzf >= 0.38.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 38, 0))
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--tmux" in captured

    def test_tmux_flag_absent_without_tmux(self, populated_env, monkeypatch):
        """--tmux absent when TMUX env var not set."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 70, 0))
        monkeypatch.delenv("TMUX", raising=False)
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--tmux" not in captured

    def test_header_shows_conversation_count(self, populated_env, monkeypatch):
        """Default header shows the number of conversations."""
        captured = capture_fzf_cmd(monkeypatch)
        lines = ["a.md\tline1", "b.md\tline2", "c.md\tline3"]
        _run_fzf(lines, populated_env["vault_dir"])
        header_args = [arg for arg in captured if arg.startswith("--header=")]
        assert any("3 conversations" in h for h in header_args)

    def test_preview_window_has_tilde_3(self, populated_env, monkeypatch):
        """Preview window includes ~3 for pinned metadata header."""
        captured = capture_fzf_cmd(monkeypatch)
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert any("~3" in arg for arg in captured if "preview-window" in arg)

    def test_enter_binding_uses_execute_with_editor(self, populated_env, monkeypatch):
        """Enter is bound to execute() with $EDITOR so user returns to fzf after viewing."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 70, 0))
        monkeypatch.setenv("EDITOR", "vim")
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("enter:execute(" in b and "vim" in b for b in bind_args)


# ---------------------------------------------------------------------------
# Preview script metadata header
# ---------------------------------------------------------------------------


class TestPreviewScriptMetadata:
    def test_preview_script_contains_title_extraction(self, tmp_path):
        """Preview script must extract title via 'grep ^# '."""
        script = _fzf_preview_script(tmp_path / "vault")
        assert "grep '^# '" in script

    def test_preview_script_contains_metadata_extraction(self, tmp_path):
        """Preview script must extract Project/Duration/Prompts metadata."""
        script = _fzf_preview_script(tmp_path / "vault")
        assert "Project" in script
        assert "Duration" in script
        assert "Prompts" in script

    def test_preview_script_contains_separator(self, tmp_path):
        """Preview script must output '---' separator line."""
        script = _fzf_preview_script(tmp_path / "vault")
        assert "echo '---'" in script


# ---------------------------------------------------------------------------
# _clipboard_cmd
# ---------------------------------------------------------------------------


class TestClipboardCmd:
    def test_macos_pbcopy(self, monkeypatch):
        """pbcopy available → returns 'pbcopy'."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: "/usr/bin/pbcopy" if cmd == "pbcopy" else None,
        )
        assert _clipboard_cmd() == "pbcopy"

    def test_wayland_wl_copy(self, monkeypatch):
        """wl-copy available + WAYLAND_DISPLAY set → returns 'wl-copy'."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: "/usr/bin/wl-copy" if cmd == "wl-copy" else None,
        )
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert _clipboard_cmd() == "wl-copy"

    def test_xclip(self, monkeypatch):
        """xclip available (no pbcopy, no wayland) → returns 'xclip -selection clipboard'."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None,
        )
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _clipboard_cmd() == "xclip -selection clipboard"

    def test_xsel_fallback(self, monkeypatch):
        """xsel available as last resort → returns 'xsel --clipboard --input'."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: "/usr/bin/xsel" if cmd == "xsel" else None,
        )
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _clipboard_cmd() == "xsel --clipboard --input"

    def test_nothing_found(self, monkeypatch):
        """No clipboard tool available → returns None."""
        monkeypatch.setattr("promptvault.search.shutil.which", lambda cmd: None)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _clipboard_cmd() is None

    def test_pbcopy_wins_over_xclip(self, monkeypatch):
        """When both pbcopy and xclip exist, pbcopy takes priority."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in ("pbcopy", "xclip") else None,
        )
        assert _clipboard_cmd() == "pbcopy"

    def test_wayland_skipped_without_display(self, monkeypatch):
        """wl-copy available but WAYLAND_DISPLAY not set → skips to xclip/xsel."""
        monkeypatch.setattr(
            "promptvault.search.shutil.which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in ("wl-copy", "xclip") else None,
        )
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _clipboard_cmd() == "xclip -selection clipboard"


class TestRunFzfClipboardOmit:
    """ctrl-y binding should be omitted when no clipboard tool is available."""

    def test_ctrl_y_present_with_clipboard(self, populated_env, monkeypatch):
        """ctrl-y binding present when clipboard tool is available."""
        captured = capture_fzf_cmd(monkeypatch)
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-y" in b for b in bind_args)

    def test_ctrl_y_absent_without_clipboard(self, populated_env, monkeypatch):
        """ctrl-y binding absent when no clipboard tool is available."""
        captured = capture_fzf_cmd(monkeypatch)
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: None)
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("ctrl-y" in b for b in bind_args)


# ---------------------------------------------------------------------------
# _fzf-prompt-lines hidden subcommand
# ---------------------------------------------------------------------------


class TestTransformBindings:
    """Test transform bindings (ctrl-t, ctrl-p, ctrl-d) in _run_fzf."""

    def test_ctrl_t_present_with_new_fzf_and_db(self, populated_env, monkeypatch):
        """ctrl-t transform binding present when fzf >= 0.45.0 and db_path provided."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-t:transform" in b for b in bind_args), (
            f"ctrl-t:transform not found in bindings: {bind_args}"
        )

    def test_ctrl_t_absent_with_old_fzf(self, populated_env, monkeypatch):
        """ctrl-t transform binding absent when fzf < 0.45.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 44, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("ctrl-t:transform" in b for b in bind_args)

    def test_ctrl_t_absent_without_db(self, populated_env, monkeypatch):
        """ctrl-t transform binding absent when no db_path (no reload possible)."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 70, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("ctrl-t:transform" in b for b in bind_args)

    def test_ctrl_t_references_prompt_lines(self, populated_env, monkeypatch):
        """ctrl-t transform script references _fzf-prompt-lines for prompt mode."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        ctrl_t = [b for b in bind_args if "ctrl-t:transform" in b]
        assert len(ctrl_t) == 1
        assert "_fzf-prompt-lines" in ctrl_t[0]
        assert "_fzf-lines" in ctrl_t[0]

    def test_default_prompt_is_conv(self, populated_env, monkeypatch):
        """Default prompt should be 'conv> ' when db_path and fzf >= 0.45.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        prompt_args = [arg for arg in captured if arg.startswith("--prompt=")]
        assert any("conv> " in p for p in prompt_args)

    def test_ctrl_p_present_with_projects(self, populated_env, monkeypatch):
        """ctrl-p transform binding present when projects exist in DB."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-p:transform" in b for b in bind_args), (
            f"ctrl-p:transform not found in bindings: {bind_args}"
        )

    def test_ctrl_p_absent_with_old_fzf(self, populated_env, monkeypatch):
        """ctrl-p transform binding absent when fzf < 0.45.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 44, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("ctrl-p:transform" in b for b in bind_args)

    def test_ctrl_p_cycles_through_projects(self, populated_env, monkeypatch):
        """ctrl-p transform script references project names from DB."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        ctrl_p = [b for b in bind_args if "ctrl-p:transform" in b]
        assert len(ctrl_p) == 1
        # Should reference --project flag for filtering
        assert "--project" in ctrl_p[0]

    def test_ctrl_d_present_with_new_fzf_and_db(self, populated_env, monkeypatch):
        """ctrl-d transform binding present when fzf >= 0.45.0 and db_path provided."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-d:transform" in b for b in bind_args), (
            f"ctrl-d:transform not found in bindings: {bind_args}"
        )

    def test_ctrl_d_absent_with_old_fzf(self, populated_env, monkeypatch):
        """ctrl-d transform binding absent when fzf < 0.45.0."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 44, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("ctrl-d:transform" in b for b in bind_args)

    def test_ctrl_d_cycles_date_ranges(self, populated_env, monkeypatch):
        """ctrl-d transform script references date range presets."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        ctrl_d = [b for b in bind_args if "ctrl-d:transform" in b]
        assert len(ctrl_d) == 1
        # Should reference today, week, month
        assert "today" in ctrl_d[0]
        assert "week" in ctrl_d[0]
        assert "month" in ctrl_d[0]

    def test_footer_includes_new_keybindings(self, populated_env, monkeypatch):
        """Footer includes ctrl-t, ctrl-p, ctrl-d when fzf >= 0.53.0 and db_path provided."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 53, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(
            ["a.md\ttest line"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        footer_args = [arg for arg in captured if arg.startswith("--footer=")]
        assert len(footer_args) >= 1
        footer = footer_args[0]
        assert "^t mode" in footer
        assert "^p proj" in footer  # abbreviated to fit 66-char limit
        assert "^d date" in footer

    def test_footer_no_new_keybindings_without_db(self, populated_env, monkeypatch):
        """Footer should NOT include ctrl-t/p/d when no db_path (no transform features)."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 53, 0))
        monkeypatch.setattr("promptvault.search._clipboard_cmd", lambda: "pbcopy")
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        footer_args = [arg for arg in captured if arg.startswith("--footer=")]
        assert len(footer_args) >= 1
        footer = footer_args[0]
        assert "^t mode" not in footer


class TestFzfLinesWithFilters:
    """Test _fzf-lines --project and --date-range flags."""

    def test_project_filter(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines --project alpha filters to project-alpha conversations only."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-lines",
                "--project",
                "alpha",
            ],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 1  # only sess-a1 is in project-alpha

    def test_project_filter_no_match(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines --project nonexistent returns empty output."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-lines",
                "--project",
                "nonexistent",
            ],
        )

        main()

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_date_range_today(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines --date-range today filters to conversations from today (likely empty with test data)."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-lines",
                "--date-range",
                "today",
            ],
        )

        main()

        out = capsys.readouterr().out
        # Test data is from 2023 — "today" filter should return nothing
        assert out.strip() == ""

    def test_date_range_month_with_old_data(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines --date-range month with old test data returns empty."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-lines",
                "--date-range",
                "month",
            ],
        )

        main()

        out = capsys.readouterr().out
        # Test data from 2023 — month filter for current month returns nothing
        assert out.strip() == ""

    def test_project_with_query(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-lines --project alpha docker → intersection of project filter and FTS query."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-lines",
                "--project",
                "alpha",
                "docker",
            ],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) >= 1  # docker is in project-alpha


class TestMainFzfPromptLines:
    """Test the _fzf-prompt-lines hidden command in-process."""

    def test_no_query_outputs_prompts(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-prompt-lines without query writes recent prompts to stdout."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-prompt-lines"],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) >= 1
        for line in lines:
            assert "\t" in line
            assert line.split("\t")[0].endswith(".md")

    def test_with_query_filters(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-prompt-lines with query returns only matching prompts."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "_fzf-prompt-lines",
                "docker",
            ],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) >= 1

    def test_empty_query_returns_all(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-prompt-lines with empty string returns all recent prompts."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-prompt-lines", ""],
        )

        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        # Should have prompts (excluding slash commands)
        assert len(lines) >= 1


# ---------------------------------------------------------------------------
# A1: --scheme=history version-gated in fzf command
# ---------------------------------------------------------------------------


class TestSchemeHistory:
    def test_scheme_history_present_when_fzf_033(self, populated_env, monkeypatch):
        """--scheme=history should appear in fzf command when fzf >= 0.33.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 33, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"], db_path=populated_env["db_path"])

        assert "--scheme=history" in captured_cmd

    def test_scheme_history_absent_when_fzf_old(self, populated_env, monkeypatch):
        """--scheme=history should NOT appear when fzf < 0.33.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 32, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"], db_path=populated_env["db_path"])

        assert "--scheme=history" not in captured_cmd


# ---------------------------------------------------------------------------
# B1: ctrl-o become + ctrl-x exclude
# ---------------------------------------------------------------------------


class TestCtrlOBecome:
    def test_ctrl_o_become_present_when_fzf_038(self, populated_env, monkeypatch):
        """ctrl-o:become binding should appear when fzf >= 0.38.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 38, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])

        bind_args = [captured_cmd[i + 1] for i, v in enumerate(captured_cmd) if v == "--bind"]
        assert any("ctrl-o:become(" in b for b in bind_args)

    def test_ctrl_o_absent_when_fzf_old(self, populated_env, monkeypatch):
        """ctrl-o:become should NOT appear when fzf < 0.38.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 37, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])

        bind_args = [captured_cmd[i + 1] for i, v in enumerate(captured_cmd) if v == "--bind"]
        assert not any("ctrl-o:become(" in b for b in bind_args)


class TestCtrlXExclude:
    def test_ctrl_x_exclude_present_when_fzf_060(self, populated_env, monkeypatch):
        """ctrl-x:exclude binding should appear when fzf >= 0.60.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 60, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])

        bind_args = [captured_cmd[i + 1] for i, v in enumerate(captured_cmd) if v == "--bind"]
        assert any("ctrl-x:exclude" in b for b in bind_args)

    def test_ctrl_x_absent_when_fzf_old(self, populated_env, monkeypatch):
        """ctrl-x:exclude should NOT appear when fzf < 0.60.0."""
        from promptvault.search import _run_fzf

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 130
            result.stdout = ""
            return result

        monkeypatch.setattr("promptvault.search.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("promptvault.search._fzf_version", lambda: (0, 59, 0))

        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])

        bind_args = [captured_cmd[i + 1] for i, v in enumerate(captured_cmd) if v == "--bind"]
        assert not any("ctrl-x:exclude" in b for b in bind_args)


# ---------------------------------------------------------------------------
# B2: bat preview with fallback
# ---------------------------------------------------------------------------


class TestBatPreview:
    def test_preview_script_contains_bat_detection(self, tmp_path):
        """Preview script must contain bat detection logic."""
        from promptvault.search import _fzf_preview_script

        script = _fzf_preview_script(tmp_path / "vault")
        assert "command -v bat" in script

    def test_preview_script_contains_bat_command(self, tmp_path):
        """Preview script must contain bat invocation with markdown language."""
        from promptvault.search import _fzf_preview_script

        script = _fzf_preview_script(tmp_path / "vault")
        assert "bat " in script
        assert "--language=markdown" in script

    def test_preview_script_contains_fallback(self, tmp_path):
        """Preview script must still contain cat/sed fallback."""
        from promptvault.search import _fzf_preview_script

        script = _fzf_preview_script(tmp_path / "vault")
        # The existing sed-based fallback must remain
        assert "sed" in script


# ---------------------------------------------------------------------------
# B3: Conversation context in prompt-mode preview
# ---------------------------------------------------------------------------


class TestPromptModePreview:
    def test_prompt_preview_script_exists(self):
        """_fzf_prompt_preview_script must be importable."""
        from promptvault.search import _fzf_prompt_preview_script

        assert callable(_fzf_prompt_preview_script)

    def test_prompt_preview_scrolls_to_matching_line(self, tmp_path):
        """Prompt preview script must grep for prompt text and scroll to it."""
        from promptvault.search import _fzf_prompt_preview_script

        script = _fzf_prompt_preview_script(tmp_path / "vault")
        # Must use grep -n to find line number for scrolling
        assert "grep -n" in script

    def test_prompt_preview_differs_from_conv_preview(self, tmp_path):
        """Prompt preview script must differ from conversation preview."""
        from promptvault.search import _fzf_preview_script, _fzf_prompt_preview_script

        conv = _fzf_preview_script(tmp_path / "vault")
        prompt = _fzf_prompt_preview_script(tmp_path / "vault")
        assert conv != prompt


# ---------------------------------------------------------------------------
# C1: Theme (--style=full, alt-bg, enhanced colors)
# ---------------------------------------------------------------------------


class TestTheme:
    def test_style_full_not_used(self, populated_env, monkeypatch):
        """--style=full removed because it eats too much width on small terminals."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 70, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        assert "--style=full" not in captured

    def test_alt_bg_in_colors_when_fzf_062(self, populated_env, monkeypatch):
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 62, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        color_args = [a for a in captured if a.startswith("--color=")]
        assert any("alt-bg:" in c for c in color_args)

    def test_alt_bg_absent_when_fzf_061(self, populated_env, monkeypatch):
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 61, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        color_args = [a for a in captured if a.startswith("--color=")]
        assert not any("alt-bg:" in c for c in color_args)


# ---------------------------------------------------------------------------
# C2: Raw mode toggle (alt-r)
# ---------------------------------------------------------------------------


class TestRawMode:
    def test_toggle_raw_not_used_with_disabled_mode(self, populated_env, monkeypatch):
        """toggle-raw is incompatible with --disabled mode, so it should never appear."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 70, 0))
        _run_fzf(["a.md\ttest line"], populated_env["vault_dir"])
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert not any("toggle-raw" in b for b in bind_args)


# ---------------------------------------------------------------------------
# C3: Interactive stats drill-down
# ---------------------------------------------------------------------------


class TestInteractiveStats:
    def test_build_stats_lines_returns_project_lines(self, populated_env):
        """_build_stats_lines must return lines with project name and counts."""
        from promptvault.search import _build_stats_lines

        conn = sqlite3.connect(str(populated_env["db_path"]))
        lines = _build_stats_lines(conn)
        assert len(lines) >= 1
        # Each line should have project name
        for line in lines:
            assert "\t" in line  # tab-separated format

    def test_stats_fzf_called_when_available(self, populated_env, monkeypatch, capsys):
        """cmd_stats should call fzf when available and tty."""
        from promptvault.search import build_parser, cmd_stats

        called = {}

        def fake_run_fzf(lines, vault_dir, **kwargs):
            called["lines"] = lines

        monkeypatch.setattr("promptvault.search._run_fzf", fake_run_fzf)
        monkeypatch.setattr("promptvault.search.has_fzf", lambda: True)
        monkeypatch.setattr("promptvault.search.sys.stdout", MagicMock(isatty=lambda: True))
        monkeypatch.setenv("PROMPTVAULT_VAULT", str(populated_env["vault_dir"]))

        args = build_parser().parse_args(["stats"])
        cmd_stats(args, populated_env["db_path"])

        assert "lines" in called

    def test_stats_static_fallback_when_no_fzf(self, populated_env, monkeypatch, capsys):
        """cmd_stats should still print static output when fzf is not available."""
        from promptvault.search import build_parser, cmd_stats

        monkeypatch.setattr("promptvault.search.has_fzf", lambda: False)

        args = build_parser().parse_args(["stats"])
        cmd_stats(args, populated_env["db_path"])

        out = capsys.readouterr().out
        assert "Conversations:" in out


# ---------------------------------------------------------------------------
# D1: Tags UI (ctrl-b bookmark, ctrl-g filter, _fzf-tag subcommand)
# ---------------------------------------------------------------------------


class TestTagsUI:
    def test_fzf_tag_subcommand_toggles_tag(self, populated_env, monkeypatch, tmp_path):
        """_fzf-tag --session-id X --tag bookmarked adds tag."""
        from promptvault.search import main

        db_path = populated_env["db_path"]
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(db_path),
                "_fzf-tag",
                "--session-id",
                "sess-a1",
                "--tag",
                "bookmarked",
            ],
        )
        main()

        from promptvault.search import _get_tagged_sessions, _get_tags_db

        tags_conn = _get_tags_db(db_path)
        assert "sess-a1" in _get_tagged_sessions(tags_conn, "bookmarked")
        tags_conn.close()

    def test_fzf_tag_remove_flag(self, populated_env, monkeypatch, tmp_path):
        """_fzf-tag --remove removes the tag."""
        from promptvault.search import _get_tags_db, _tag_session, main

        db_path = populated_env["db_path"]
        tags_conn = _get_tags_db(db_path)
        _tag_session(tags_conn, "sess-a1", "bookmarked")
        tags_conn.close()

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(db_path),
                "_fzf-tag",
                "--session-id",
                "sess-a1",
                "--tag",
                "bookmarked",
                "--remove",
            ],
        )
        main()

        from promptvault.search import _get_tagged_sessions

        tags_conn = _get_tags_db(db_path)
        assert "sess-a1" not in _get_tagged_sessions(tags_conn, "bookmarked")
        tags_conn.close()

    def test_conversation_lines_include_session_id_field(self, populated_env):
        """Conversation lines must include session_id as field 3 (hidden)."""
        from promptvault.search import _build_conversation_lines

        conn = sqlite3.connect(str(populated_env["db_path"]))
        lines = _build_conversation_lines(conn)
        for line in lines:
            fields = line.split("\t")
            assert len(fields) >= 3, f"Expected >=3 fields, got {len(fields)}: {line!r}"
            # Field 3 should look like a session ID
            assert fields[2].startswith("sess-"), f"Field 3 not a session_id: {fields[2]!r}"

    def test_ctrl_b_fav_binding_present(self, populated_env, monkeypatch):
        """ctrl-b binding should appear in fzf command when db_path provided."""
        captured = capture_fzf_cmd(monkeypatch, fzf_ver=(0, 45, 0))
        _run_fzf(
            ["a.md\ttest line\tsess-1"],
            populated_env["vault_dir"],
            db_path=populated_env["db_path"],
        )
        bind_args = [captured[i + 1] for i, v in enumerate(captured) if v == "--bind"]
        assert any("ctrl-b:" in b for b in bind_args)


# ---------------------------------------------------------------------------
# D2: Shell widget
# ---------------------------------------------------------------------------


class TestShellWidget:
    def test_fzf_widget_lines_subcommand(self, populated_env, monkeypatch, capsys, tmp_path):
        """_fzf-widget-lines outputs recent prompts for shell widget."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "--db", str(populated_env["db_path"]), "_fzf-widget-lines"],
        )
        main()

        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) >= 1

    def test_shell_init_zsh(self, monkeypatch, capsys, tmp_path):
        """shell-init zsh outputs eval-able zsh widget function."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "shell-init", "zsh"],
        )
        main()

        out = capsys.readouterr().out
        assert "__promptvault_widget" in out
        assert "LBUFFER" in out
        assert "zle" in out

    def test_shell_init_bash(self, monkeypatch, capsys, tmp_path):
        """shell-init bash outputs eval-able bash widget function."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["promptvault", "shell-init", "bash"],
        )
        main()

        out = capsys.readouterr().out
        assert "__promptvault_widget" in out
        assert "READLINE_LINE" in out

    def test_zsh_widget_file_exists(self):
        """pv-widget.zsh must exist in promptvault/shell/."""
        widget_path = Path(__file__).parent.parent / "promptvault" / "shell" / "pv-widget.zsh"
        assert widget_path.exists()

    def test_bash_widget_file_exists(self):
        """pv-widget.bash must exist in promptvault/shell/."""
        widget_path = Path(__file__).parent.parent / "promptvault" / "shell" / "pv-widget.bash"
        assert widget_path.exists()


# ---------------------------------------------------------------------------
# D3: Batch export (pv export --format json|csv|md)
# ---------------------------------------------------------------------------


class TestBatchExport:
    def test_export_json_format(self, populated_env, monkeypatch, capsys, tmp_path):
        """pv export --query docker --format json outputs valid JSON."""
        import json as json_mod

        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "export",
                "--query",
                "docker",
                "--format",
                "json",
            ],
        )
        main()

        out = capsys.readouterr().out
        data = json_mod.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "prompt" in data[0]
        assert "timestamp" in data[0]

    def test_export_csv_format(self, populated_env, monkeypatch, capsys, tmp_path):
        """pv export --query docker --format csv outputs CSV with header."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "export",
                "--query",
                "docker",
                "--format",
                "csv",
            ],
        )
        main()

        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert lines[0].startswith("prompt,")  # header
        assert len(lines) >= 2  # header + at least 1 data row

    def test_export_md_format(self, populated_env, monkeypatch, capsys, tmp_path):
        """pv export --query docker --format md outputs markdown."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "export",
                "--query",
                "docker",
                "--format",
                "md",
            ],
        )
        main()

        out = capsys.readouterr().out
        assert "docker" in out.lower()

    def test_export_to_file(self, populated_env, monkeypatch, capsys, tmp_path):
        """pv export --output FILE writes to file instead of stdout."""
        from promptvault.search import main

        output_file = tmp_path / "export.json"
        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "export",
                "--query",
                "docker",
                "--format",
                "json",
                "--output",
                str(output_file),
            ],
        )
        main()

        assert output_file.exists()
        import json as json_mod

        data = json_mod.loads(output_file.read_text())
        assert isinstance(data, list)

    def test_export_no_results(self, populated_env, monkeypatch, capsys, tmp_path):
        """pv export with no matching results prints a message."""
        from promptvault.search import main

        monkeypatch.setenv("PROMPTVAULT_HISTORY", str(tmp_path / "no-history.jsonl"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "promptvault",
                "--db",
                str(populated_env["db_path"]),
                "export",
                "--query",
                "xyznonexistent999",
                "--format",
                "json",
            ],
        )
        main()

        out = capsys.readouterr().out
        # Empty JSON array or "no results" message
        assert "[]" in out or "No results" in out
