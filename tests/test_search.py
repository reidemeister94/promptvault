"""Tests for promptvault.search module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from promptvault.sync import build_database, parse_history
from promptvault.search import (
    _build_conversation_lines,
    _fts_prepare_query,
    _fts_search,
    _short_project,
    _short_title,
    clean_prompt_text,
    truncate,
)


@pytest.fixture
def db_path(tmp_history: Path, tmp_output: Path) -> Path:
    """Build a test database from synthetic history."""
    sessions = parse_history(tmp_history)
    md_paths = {sid: f"2023/11/{sid}.md" for sid in sessions}
    db = tmp_output / "prompts.db"
    build_database(sessions, md_paths, db)
    return db


class TestFTSSearch:
    def test_search_finds_matching_prompt(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            """
            SELECT p.prompt_text
            FROM prompts_fts
            JOIN prompts p ON prompts_fts.rowid = p.id
            WHERE prompts_fts MATCH 'pytest'
            """,
        ).fetchall()
        assert len(rows) == 1
        assert "pytest" in rows[0][0]

    def test_search_with_bm25_ranking(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            """
            SELECT p.prompt_text, bm25(prompts_fts) as rank
            FROM prompts_fts
            JOIN prompts p ON prompts_fts.rowid = p.id
            WHERE prompts_fts MATCH 'bug'
            ORDER BY rank
            """,
        ).fetchall()
        assert len(rows) >= 1

    def test_search_no_results(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT * FROM prompts_fts WHERE prompts_fts MATCH 'xyznonexistent'",
        ).fetchall()
        assert len(rows) == 0

    def test_prefix_search_works(self, db_path: Path):
        """Partial word 'pytes' should match 'pytest' via prefix wildcard."""
        conn = sqlite3.connect(str(db_path))
        fts_q = _fts_prepare_query("pytes")
        rows = conn.execute(
            "SELECT * FROM prompts_fts WHERE prompts_fts MATCH ?",
            (fts_q,),
        ).fetchall()
        assert len(rows) >= 1

    def test_prefix_search_single_char(self, db_path: Path):
        """Single char 'a' should match via prefix."""
        conn = sqlite3.connect(str(db_path))
        fts_q = _fts_prepare_query("a")
        rows = conn.execute(
            "SELECT * FROM prompts_fts WHERE prompts_fts MATCH ?",
            (fts_q,),
        ).fetchall()
        assert len(rows) >= 1

    def test_fts_search_with_prefix(self, db_path: Path):
        """_fts_search should find results for partial words."""
        conn = sqlite3.connect(str(db_path))
        rows = _fts_search(conn, "authenti")  # partial "authentication"
        assert len(rows) >= 1
        assert any("authentication" in r[0].lower() for r in rows)


class TestFtsPrepareQuery:
    def test_adds_wildcard_to_last_word(self):
        assert _fts_prepare_query("super") == "super*"

    def test_adds_wildcard_to_multiword(self):
        assert _fts_prepare_query("api parity") == "api parity*"

    def test_empty_query(self):
        assert _fts_prepare_query("") == ""

    def test_single_word(self):
        assert _fts_prepare_query("docker") == "docker*"


class TestBuildConversationLines:
    def test_returns_lines_without_query(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        lines = _build_conversation_lines(conn)
        # Should return conversations with prompt_count > 0
        assert len(lines) >= 1
        # Each line has tab-separated fields: md_path\tvisible
        for line in lines:
            parts = line.split("\t")
            assert len(parts) == 2
            assert parts[0].endswith(".md")  # md_path

    def test_returns_lines_with_query(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        lines = _build_conversation_lines(conn, "pytest")
        assert len(lines) >= 1

    def test_returns_empty_for_nonsense_query(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        lines = _build_conversation_lines(conn, "xyznonexistent")
        assert len(lines) == 0

    def test_prefix_query_finds_results(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        lines = _build_conversation_lines(conn, "pytes")  # partial "pytest"
        assert len(lines) >= 1

    def test_filters_empty_sessions(self, db_path: Path):
        """Sessions with only slash commands (0 real prompts) should be excluded."""
        conn = sqlite3.connect(str(db_path))
        lines = _build_conversation_lines(conn)
        for line in lines:
            visible = line.split("\t")[1]
            # Should not contain "0p"
            assert " 0p " not in visible


class TestRecentQuery:
    def test_recent_returns_ordered(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT timestamp FROM prompts ORDER BY timestamp DESC LIMIT 5",
        ).fetchall()
        timestamps = [r[0] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)


class TestListConversations:
    def test_list_all(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT * FROM conversations ORDER BY start_ts DESC").fetchall()
        assert len(rows) == 4

    def test_filter_by_project(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT * FROM conversations WHERE project LIKE ?",
            ("%project-b%",),
        ).fetchall()
        assert len(rows) == 1


class TestStats:
    def test_counts(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        prompt_count = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
        assert conv_count == 4
        assert prompt_count > 0

    def test_display_name_column_exists(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT display_name FROM conversations LIMIT 1").fetchone()
        assert row is not None
        assert len(row[0]) > 0


class TestCleanPromptText:
    def test_strips_pasted_text_markers(self):
        assert "analyze" in clean_prompt_text("[Pasted text #1 +3 lines] analyze")

    def test_image_only_returns_label(self):
        assert clean_prompt_text("[Image #14]") == "(image)"

    def test_multiple_images_returns_plural(self):
        assert clean_prompt_text("[Image #1][Image #2]") == "(images)"

    def test_image_with_text_keeps_text(self):
        result = clean_prompt_text("[Image #1] check this screenshot")
        assert "check this screenshot" in result
        assert "[Image" not in result

    def test_empty_after_cleanup(self):
        assert clean_prompt_text("[Pasted text #1]") == ""


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello world") == "hello world"

    def test_long_text_truncated(self):
        long = "a" * 200
        result = truncate(long)
        assert len(result) == 123  # 120 + "..."
        assert result.endswith("...")

    def test_empty_returns_label(self):
        assert truncate("") == "(empty)"


class TestShortTitle:
    def test_limits_to_4_words(self):
        result = _short_title("one two three four five six seven")
        words = result.split()
        assert len(words) == 4

    def test_short_text_unchanged(self):
        assert _short_title("hello world") == "hello world"

    def test_max_35_chars(self):
        result = _short_title("verylongword " * 5)
        assert len(result) <= 35

    def test_cleans_markers(self):
        result = _short_title("[Image #1] check this screenshot now please")
        assert "[Image" not in result
        assert len(result.split()) <= 4


class TestShortProject:
    def test_home_dir_returns_tilde(self):
        from pathlib import Path

        home = str(Path.home())
        assert _short_project(home) == "~"

    def test_empty_returns_tilde(self):
        assert _short_project("") == "~"

    def test_short_name_unchanged(self):
        assert _short_project("/Users/test/myproject") == "myproject"

    def test_long_name_truncated(self):
        result = _short_project("/Users/test/a-very-long-project-name-here")
        assert len(result) <= 20
        assert result.endswith("…")
