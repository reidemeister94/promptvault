"""Tests for promptvault.search module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from promptvault.sync import build_database, parse_history


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
