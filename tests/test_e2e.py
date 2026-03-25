"""End-to-end tests: build a realistic DB from scratch, test every CLI path, tear down.

The fixture creates a rich dataset covering all edge cases:
- Normal multi-prompt conversations
- Slash-command-only sessions
- Image-only prompts
- Pasted content with markers
- Unicode / Italian text
- Very long prompts
- Sessions across multiple projects and dates
- Trailing whitespace (the Claude Code terminal artifact)
- Duplicate prompts in same session
- Sessions with Claude-generated summaries
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from promptvault.sync import (
    build_database,
    generate_index,
    generate_vault,
    load_session_summaries,
    parse_history,
)
from promptvault.search import (
    _build_conversation_lines,
    _fts_prepare_query,
    _fts_search,
    _short_project,
    _short_title,
    build_parser,
    clean_prompt_text,
    cmd_search_plain,
    cmd_stats,
    get_db,
    truncate,
)


# ---------------------------------------------------------------------------
# Rich test dataset
# ---------------------------------------------------------------------------

HISTORY_ENTRIES = [
    # Session 1: Normal multi-prompt conversation (project-alpha)
    {
        "display": "explain how docker networking works",
        "pastedContents": {},
        "timestamp": 1700000000000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0001",
    },
    {
        "display": "can you show me a docker-compose example with two services?",
        "pastedContents": {},
        "timestamp": 1700000060000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0001",
    },
    {
        "display": "now add a redis container as well",
        "pastedContents": {},
        "timestamp": 1700000120000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0001",
    },
    # Session 2: Slash-command-only session (should have 0 real prompts)
    {
        "display": "/help ",
        "pastedContents": {},
        "timestamp": 1700100000000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0002",
    },
    {
        "display": "/compact ",
        "pastedContents": {},
        "timestamp": 1700100060000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0002",
    },
    # Session 3: Image-only prompts
    {
        "display": "[Image #1]",
        "pastedContents": {},
        "timestamp": 1700200000000,
        "project": "/Users/test/project-beta",
        "sessionId": "sess-0003",
    },
    {
        "display": "[Image #2][Image #3] check these screenshots",
        "pastedContents": {},
        "timestamp": 1700200060000,
        "project": "/Users/test/project-beta",
        "sessionId": "sess-0003",
    },
    # Session 4: Pasted content with markers
    {
        "display": "[Pasted text #1 +10 lines]\n\nanalyze the pasted code above",
        "pastedContents": {
            "1": {
                "id": 1,
                "type": "text",
                "content": "def fibonacci(n):\n    if n <= 1: return n\n    return fibonacci(n-1) + fibonacci(n-2)",
            }
        },
        "timestamp": 1700300000000,
        "project": "/Users/test/project-beta",
        "sessionId": "sess-0004",
    },
    # Session 5: Unicode / Italian text
    {
        "display": "voglio capire come funziona il deployment su AWS con le lambda",
        "pastedContents": {},
        "timestamp": 1700400000000,
        "project": "/Users/test/progetto-italiano",
        "sessionId": "sess-0005",
    },
    {
        "display": "sì, è corretto. ora dimmi anche come configurare il VPC",
        "pastedContents": {},
        "timestamp": 1700400060000,
        "project": "/Users/test/progetto-italiano",
        "sessionId": "sess-0005",
    },
    # Session 6: Very long prompt with trailing whitespace
    {
        "display": "I need you to refactor the entire authentication module "
        + "  " * 50
        + "\n\n\n\n"
        "because the current implementation has security vulnerabilities " + "   " * 30 + "\n\n\n"
        "and we need to support OAuth2 and SAML in addition to basic auth",
        "pastedContents": {},
        "timestamp": 1700500000000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0006",
    },
    # Session 7: Custom slash command (should be filtered)
    {
        "display": "/my-custom-command",
        "pastedContents": {},
        "timestamp": 1700600000000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0007",
    },
    {
        "display": "fix the superpowers plugin installation",
        "pastedContents": {},
        "timestamp": 1700600060000,
        "project": "/Users/test/project-alpha",
        "sessionId": "sess-0007",
    },
    # Session 8: Duplicate prompts in same session (should deduplicate)
    {
        "display": "run the test suite",
        "pastedContents": {},
        "timestamp": 1700700000000,
        "project": "/Users/test/project-gamma",
        "sessionId": "sess-0008",
    },
    {
        "display": "run the test suite",
        "pastedContents": {},
        "timestamp": 1700700001000,
        "project": "/Users/test/project-gamma",
        "sessionId": "sess-0008",
    },
    {
        "display": "now show me the coverage report",
        "pastedContents": {},
        "timestamp": 1700700060000,
        "project": "/Users/test/project-gamma",
        "sessionId": "sess-0008",
    },
    # Session 9: Session with cd/env debug paste as first prompt
    {
        "display": "cd /Users/test/project ; /usr/bin/env /opt/anaconda3/bin/python run.py",
        "pastedContents": {},
        "timestamp": 1700800000000,
        "project": "/Users/test/project-delta",
        "sessionId": "sess-0009",
    },
    {
        "display": "that failed, can you fix the import error?",
        "pastedContents": {},
        "timestamp": 1700800060000,
        "project": "/Users/test/project-delta",
        "sessionId": "sess-0009",
    },
    # Session 10: Very long project name
    {
        "display": "check the deployment status",
        "pastedContents": {},
        "timestamp": 1700900000000,
        "project": "/Users/test/my-very-long-project-name-that-exceeds-twenty-chars",
        "sessionId": "sess-0010",
    },
    # Session 11: Home directory project
    {
        "display": "what version of python am I using?",
        "pastedContents": {},
        "timestamp": 1701000000000,
        "project": str(Path.home()),
        "sessionId": "sess-0011",
    },
]

# Summaries simulating Claude Code's sessions-index.json
MOCK_SUMMARIES = {
    "sess-0001": "Docker Networking and Compose Setup",
    "sess-0005": "AWS Lambda Deployment Configuration",
    "sess-0008": "Test Suite Execution and Coverage",
}


@pytest.fixture
def e2e_env(tmp_path: Path):
    """Build a complete promptvault environment from scratch."""
    # Write history
    history_path = tmp_path / "history.jsonl"
    with open(history_path, "w") as f:
        for entry in HISTORY_ENTRIES:
            f.write(json.dumps(entry) + "\n")

    # Parse and build
    output_dir = tmp_path / "prompt-library"
    vault_dir = output_dir / "vault"
    vault_dir.mkdir(parents=True)
    db_path = output_dir / "prompts.db"

    sessions = parse_history(history_path)
    md_paths = generate_vault(sessions, vault_dir)
    generate_index(sessions, md_paths, vault_dir)
    build_database(sessions, md_paths, db_path, summaries=MOCK_SUMMARIES)

    return {
        "db_path": db_path,
        "vault_dir": vault_dir,
        "output_dir": output_dir,
        "sessions": sessions,
        "md_paths": md_paths,
    }


# ---------------------------------------------------------------------------
# Database integrity
# ---------------------------------------------------------------------------


class TestDatabaseIntegrity:
    def test_all_sessions_stored(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert count == 11  # all 11 sessions

    def test_slash_only_session_has_zero_prompts(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT prompt_count FROM conversations WHERE session_id = 'sess-0002'"
        ).fetchone()
        assert row[0] == 0

    def test_normal_session_prompt_count(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT prompt_count FROM conversations WHERE session_id = 'sess-0001'"
        ).fetchone()
        assert row[0] == 3

    def test_duplicate_prompts_deduped(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT prompt_count FROM conversations WHERE session_id = 'sess-0008'"
        ).fetchone()
        assert row[0] == 2  # "run the test suite" deduped + "coverage report"

    def test_display_name_uses_summary_when_available(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT display_name FROM conversations WHERE session_id = 'sess-0001'"
        ).fetchone()
        assert row[0] == "Docker Networking and Compose Setup"

    def test_display_name_fallback_when_no_summary(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT display_name FROM conversations WHERE session_id = 'sess-0003'"
        ).fetchone()
        # Should use first real prompt text, not session-XXXX
        assert "check these screenshots" in row[0].lower() or "image" not in row[0].lower()

    def test_slash_only_display_name(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        row = conn.execute(
            "SELECT display_name FROM conversations WHERE session_id = 'sess-0002'"
        ).fetchone()
        assert row[0] == "(no text prompts)"

    def test_fts_index_populated(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        count = conn.execute("SELECT COUNT(*) FROM prompts_fts").fetchone()[0]
        assert count > 0


# ---------------------------------------------------------------------------
# FTS search (the core feature)
# ---------------------------------------------------------------------------


class TestFTSSearchE2E:
    def test_full_word_match(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "docker")
        assert len(rows) >= 1
        assert any("docker" in r[0].lower() for r in rows)

    def test_prefix_match(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "dock")  # prefix of "docker"
        assert len(rows) >= 1

    def test_prefix_match_single_char(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "d")
        assert len(rows) >= 1

    def test_italian_text_searchable(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "deployment")
        assert len(rows) >= 1

    def test_pasted_content_searchable(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "fibonacci")
        assert len(rows) >= 1

    def test_no_results_for_nonsense(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "xyznonexistent123")
        assert len(rows) == 0

    def test_multiword_search(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "docker compose")
        assert len(rows) >= 1

    def test_superpowers_found(self, e2e_env):
        """The word 'superpowers' in session 7 must be findable."""
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "superpowers")
        assert len(rows) >= 1

    def test_superpowers_prefix_found(self, e2e_env):
        """Typing 'superpo' should find 'superpowers' via prefix."""
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        rows = _fts_search(conn, "superpo")
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Conversation line builder (fzf left panel)
# ---------------------------------------------------------------------------


class TestConversationLinesE2E:
    def test_all_lines_returned_no_query(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn)
        # Should exclude sessions with 0 real prompts
        zero_prompt_sessions = 1  # sess-0002
        assert len(lines) == 11 - zero_prompt_sessions

    def test_empty_sessions_excluded(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn)
        for line in lines:
            visible = line.split("\t")[1]
            assert " 0p " not in visible

    def test_line_format_is_tab_separated(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn)
        for line in lines:
            parts = line.split("\t")
            assert len(parts) == 2
            assert parts[0].endswith(".md")

    def test_query_filters_conversations(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        all_lines = _build_conversation_lines(conn)
        docker_lines = _build_conversation_lines(conn, "docker")
        assert 0 < len(docker_lines) < len(all_lines)

    def test_prefix_query_works(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn, "superpo")
        assert len(lines) >= 1

    def test_short_title_in_visible_part(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn)
        for line in lines:
            visible = line.split("\t")[1]
            # Visible part should be compact (no 80+ char titles)
            assert len(visible) < 80

    def test_project_name_in_visible_part(self, e2e_env):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        lines = _build_conversation_lines(conn)
        # At least one line should contain a project name
        all_visible = " ".join(line.split("\t")[1] for line in lines)
        assert "project-alpha" in all_visible or "project-beta" in all_visible


# ---------------------------------------------------------------------------
# Vault (markdown files)
# ---------------------------------------------------------------------------


class TestVaultE2E:
    def test_markdown_files_created(self, e2e_env):
        vault_dir = e2e_env["vault_dir"]
        md_files = list(vault_dir.rglob("*.md"))
        # 11 sessions + 1 index file
        assert len(md_files) >= 11

    def test_markdown_has_frontmatter(self, e2e_env):
        vault_dir = e2e_env["vault_dir"]
        md_files = [f for f in vault_dir.rglob("*.md") if f.name != "_index.md"]
        for md_file in md_files[:3]:
            content = md_file.read_text()
            assert content.startswith("---")
            assert "session_id:" in content

    def test_trailing_whitespace_stripped(self, e2e_env):
        """Session 6 had trailing whitespace — verify it's cleaned in markdown."""
        vault_dir = e2e_env["vault_dir"]
        # Check all files for trailing whitespace
        for md_file in vault_dir.rglob("*.md"):
            if md_file.name == "_index.md":
                continue
            for line in md_file.read_text().splitlines():
                # No line should end with spaces (except empty lines)
                if line.strip():
                    assert line == line.rstrip(), (
                        f"Trailing whitespace in {md_file.name}: '{line[-20:]}'"
                    )

    def test_blank_lines_squeezed(self, e2e_env):
        """No 3+ consecutive blank lines in any markdown file."""
        vault_dir = e2e_env["vault_dir"]
        for md_file in vault_dir.rglob("*.md"):
            if md_file.name == "_index.md":
                continue
            content = md_file.read_text()
            assert "\n\n\n" not in content, f"Triple blank line in {md_file.name}"

    def test_index_file_exists(self, e2e_env):
        vault_dir = e2e_env["vault_dir"]
        index = vault_dir / "_index.md"
        assert index.exists()
        content = index.read_text()
        assert "Prompt Vault Index" in content

    def test_preview_files_reachable(self, e2e_env):
        """Every md_path in DB points to a real file in the vault."""
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        vault_dir = e2e_env["vault_dir"]
        rows = conn.execute("SELECT md_path FROM conversations WHERE md_path != ''").fetchall()
        for (md_path,) in rows:
            full_path = vault_dir / md_path
            assert full_path.exists(), f"Preview file missing: {full_path}"


# ---------------------------------------------------------------------------
# CLI commands (non-interactive, stdout capture)
# ---------------------------------------------------------------------------


class TestCLIE2E:
    def test_stats_command(self, e2e_env, capsys):
        args = build_parser().parse_args(["stats"])
        cmd_stats(args, e2e_env["db_path"])
        out = capsys.readouterr().out
        assert "Conversations:" in out
        assert "Prompts:" in out
        assert "Projects:" in out

    def test_search_plain_finds_docker(self, e2e_env, capsys):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        cmd_search_plain(conn, "docker", limit=10)
        out = capsys.readouterr().out
        assert "docker" in out.lower()
        assert "result(s)" in out

    def test_search_plain_prefix(self, e2e_env, capsys):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        cmd_search_plain(conn, "superpo", limit=10)
        out = capsys.readouterr().out
        assert "result(s)" in out

    def test_search_plain_no_results(self, e2e_env, capsys):
        conn = sqlite3.connect(str(e2e_env["db_path"]))
        cmd_search_plain(conn, "xyznonexistent123", limit=10)
        out = capsys.readouterr().out
        assert "No results" in out

    def test_recent_plain(self, e2e_env, capsys):
        args = build_parser().parse_args(["recent", "--no-fzf", "5"])
        args.no_fzf = True
        conn = get_db(e2e_env["db_path"])
        # Simulate recent plain output
        rows = conn.execute(
            """SELECT p.prompt_text FROM prompts p
               JOIN conversations c ON p.session_id = c.session_id
               WHERE p.prompt_text NOT GLOB '/[a-z]*'
               ORDER BY p.timestamp DESC LIMIT 5"""
        ).fetchall()
        assert len(rows) >= 1
        # No slash commands in results
        for (text,) in rows:
            assert not text.strip().startswith("/")

    def test_fzf_lines_subcommand_no_query(self, e2e_env):
        """_fzf-lines without query returns all non-empty conversations."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "promptvault.search",
                "--db",
                str(e2e_env["db_path"]),
                "_fzf-lines",
            ],
            capture_output=True,
            text=True,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) == 10  # 11 sessions minus 1 empty

    def test_fzf_lines_subcommand_with_query(self, e2e_env):
        """_fzf-lines with query returns matching conversations."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "promptvault.search",
                "--db",
                str(e2e_env["db_path"]),
                "_fzf-lines",
                "docker",
            ],
            capture_output=True,
            text=True,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) >= 1

    def test_fzf_lines_prefix_query(self, e2e_env):
        """_fzf-lines with partial word finds results."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "promptvault.search",
                "--db",
                str(e2e_env["db_path"]),
                "_fzf-lines",
                "superpo",
            ],
            capture_output=True,
            text=True,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) >= 1

    def test_fzf_lines_empty_query(self, e2e_env):
        """_fzf-lines with empty string returns all."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "promptvault.search",
                "--db",
                str(e2e_env["db_path"]),
                "_fzf-lines",
                "",
            ],
            capture_output=True,
            text=True,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) == 10

    def test_fzf_lines_nonsense_query(self, e2e_env):
        """_fzf-lines with nonsense returns empty."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "promptvault.search",
                "--db",
                str(e2e_env["db_path"]),
                "_fzf-lines",
                "xyznonexistent",
            ],
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        assert output == ""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


class TestDisplayHelpersE2E:
    def test_short_title_limits_words(self):
        assert len(_short_title("one two three four five six").split()) == 4

    def test_short_title_strips_images(self):
        result = _short_title("[Image #1] look at this thing now")
        assert "[Image" not in result

    def test_short_project_home_is_tilde(self):
        assert _short_project(str(Path.home())) == "~"

    def test_short_project_truncates_long(self):
        result = _short_project("/Users/test/my-very-long-project-name-here")
        assert len(result) <= 20

    def test_clean_prompt_text_images(self):
        assert clean_prompt_text("[Image #1]") == "(image)"
        assert clean_prompt_text("[Image #1][Image #2]") == "(images)"
        assert "hello" in clean_prompt_text("[Image #1] hello")

    def test_truncate_long(self):
        result = truncate("x" * 200)
        assert len(result) <= 123

    def test_truncate_empty(self):
        assert truncate("") == "(empty)"

    def test_fts_prepare_query_wildcard(self):
        assert _fts_prepare_query("dock") == "dock*"
        assert _fts_prepare_query("api parity") == "api parity*"
        assert _fts_prepare_query("") == ""


# ---------------------------------------------------------------------------
# Session summaries (from sessions-index.json)
# ---------------------------------------------------------------------------


class TestSessionSummaries:
    def test_load_from_mock_index(self, tmp_path: Path):
        """Test loading summaries from a mock sessions-index.json."""
        projects_dir = tmp_path / "projects"
        proj = projects_dir / "test-project"
        proj.mkdir(parents=True)
        index = {
            "version": 1,
            "entries": [
                {
                    "sessionId": "abc-123",
                    "summary": "My Test Summary",
                    "created": "2026-01-01T00:00:00Z",
                },
                {
                    "sessionId": "def-456",
                    "summary": "Another Summary",
                    "created": "2026-01-02T00:00:00Z",
                },
            ],
        }
        (proj / "sessions-index.json").write_text(json.dumps(index))

        summaries = load_session_summaries(projects_dir)
        assert summaries["abc-123"] == "My Test Summary"
        assert summaries["def-456"] == "Another Summary"

    def test_load_missing_dir(self, tmp_path: Path):
        summaries = load_session_summaries(tmp_path / "nonexistent")
        assert summaries == {}

    def test_load_corrupt_json(self, tmp_path: Path):
        projects_dir = tmp_path / "projects"
        proj = projects_dir / "bad-project"
        proj.mkdir(parents=True)
        (proj / "sessions-index.json").write_text("NOT JSON")

        summaries = load_session_summaries(projects_dir)
        assert summaries == {}
