"""Tests for promptvault.sync module."""

from __future__ import annotations

from pathlib import Path

from promptvault.sync import (
    build_database,
    generate_index,
    generate_vault,
    is_slash_command,
    make_conversation_name,
    parse_history,
    resolve_pasted_content,
    slugify,
)


class TestParseHistory:
    def test_parses_all_sessions(self, tmp_history: Path):
        sessions = parse_history(tmp_history)
        assert len(sessions) == 4

    def test_groups_prompts_by_session(self, tmp_history: Path):
        sessions = parse_history(tmp_history)
        assert len(sessions["aaaa-1111-2222-3333"]) == 3
        assert len(sessions["bbbb-4444-5555-6666"]) == 2
        assert len(sessions["cccc-7777-8888-9999"]) == 1

    def test_prompts_sorted_by_timestamp(self, tmp_history: Path):
        sessions = parse_history(tmp_history)
        prompts = sessions["aaaa-1111-2222-3333"]
        timestamps = [p["timestamp"] for p in prompts]
        assert timestamps == sorted(timestamps)


class TestSlashCommand:
    def test_detects_help(self):
        assert is_slash_command("/help ") is True

    def test_detects_compact(self):
        assert is_slash_command("/compact ") is True

    def test_regular_prompt_not_command(self):
        assert is_slash_command("explain this code") is False

    def test_slash_in_text_not_command(self):
        assert is_slash_command("use http://example.com") is False


class TestResolvePastedContent:
    def test_replaces_placeholder_with_content(self):
        entry = {
            "display": "[Pasted text #1 +3 lines]\n\nanalyze this",
            "pastedContents": {
                "1": {"id": 1, "type": "text", "content": "def hello():\n    return True"}
            },
        }
        result = resolve_pasted_content(entry)
        assert "def hello():" in result
        assert "[Pasted text" not in result
        assert "analyze this" in result

    def test_no_pasted_contents(self):
        entry = {"display": "just a normal prompt", "pastedContents": {}}
        assert resolve_pasted_content(entry) == "just a normal prompt"

    def test_multiple_pastes(self):
        entry = {
            "display": "[Pasted text #1] and [Pasted text #2 +5 lines]",
            "pastedContents": {
                "1": {"id": 1, "type": "text", "content": "FIRST"},
                "2": {"id": 2, "type": "text", "content": "SECOND"},
            },
        }
        result = resolve_pasted_content(entry)
        assert "FIRST" in result
        assert "SECOND" in result
        assert "[Pasted text" not in result

    def test_resolved_in_parse_history(self, tmp_history: Path):
        sessions = parse_history(tmp_history)
        # Session dddd has pasted content
        prompts = sessions["dddd-0000-1111-2222"]
        assert "def hello():" in prompts[0]["display"]
        assert "[Pasted text" not in prompts[0]["display"]


class TestSlugify:
    def test_basic_slug(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        assert slugify("fix the bug! @#$%") == "fix-the-bug"

    def test_truncates_long_text(self):
        result = slugify("a" * 100, max_length=20)
        assert len(result) <= 20

    def test_empty_string(self):
        assert slugify("") == ""

    def test_unicode_stripped(self):
        assert slugify("café résumé") == "caf-rsum"


class TestMakeConversationName:
    def test_uses_first_real_prompt(self):
        prompts = [
            {"display": "/help ", "timestamp": 1},
            {"display": "explain pytest fixtures", "timestamp": 2},
        ]
        name = make_conversation_name(prompts, "test-session-id")
        assert name == "explain-pytest-fixtures"

    def test_fallback_to_session_id(self):
        prompts = [
            {"display": "/help ", "timestamp": 1},
            {"display": "/compact ", "timestamp": 2},
        ]
        name = make_conversation_name(prompts, "abcdef12-3456-7890")
        assert name == "session-abcdef12"


class TestGenerateVault:
    def test_creates_markdown_files(self, tmp_history: Path, tmp_output: Path):
        sessions = parse_history(tmp_history)
        vault_dir = tmp_output / "vault"
        vault_dir.mkdir()
        md_paths = generate_vault(sessions, vault_dir)

        assert len(md_paths) == 4
        for md_path in md_paths.values():
            full_path = vault_dir / md_path
            assert full_path.exists()
            content = full_path.read_text()
            assert "---" in content  # frontmatter
            assert "session_id:" in content

    def test_date_based_directories(self, tmp_history: Path, tmp_output: Path):
        sessions = parse_history(tmp_history)
        vault_dir = tmp_output / "vault"
        vault_dir.mkdir()
        generate_vault(sessions, vault_dir)

        # All test data is from November 2023 (timestamp 1700000000000)
        assert (vault_dir / "2023" / "11").is_dir()

    def test_markdown_contains_prompts(self, tmp_history: Path, tmp_output: Path):
        sessions = parse_history(tmp_history)
        vault_dir = tmp_output / "vault"
        vault_dir.mkdir()
        md_paths = generate_vault(sessions, vault_dir)

        # Check session aaaa has both real prompts
        md_path = vault_dir / md_paths["aaaa-1111-2222-3333"]
        content = md_path.read_text()
        assert "explain how to use pytest fixtures" in content
        assert "can you add type hints" in content
        assert "prompt_count: 2" in content  # /help filtered from count


class TestGenerateIndex:
    def test_creates_index_file(self, tmp_history: Path, tmp_output: Path):
        sessions = parse_history(tmp_history)
        vault_dir = tmp_output / "vault"
        vault_dir.mkdir()
        md_paths = generate_vault(sessions, vault_dir)
        generate_index(sessions, md_paths, vault_dir)

        index_path = vault_dir / "_index.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "Prompt Vault Index" in content
        assert "Total conversations:" in content


class TestBuildDatabase:
    def test_creates_database(self, tmp_history: Path, tmp_output: Path):
        sessions = parse_history(tmp_history)
        md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
        db_path = tmp_output / "prompts.db"
        build_database(sessions, md_paths, db_path)

        assert db_path.exists()

    def test_conversation_count(self, tmp_history: Path, tmp_output: Path):
        import sqlite3

        sessions = parse_history(tmp_history)
        md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
        db_path = tmp_output / "prompts.db"
        build_database(sessions, md_paths, db_path)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert count == 4

    def test_fts_search_works(self, tmp_history: Path, tmp_output: Path):
        import sqlite3

        sessions = parse_history(tmp_history)
        md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
        db_path = tmp_output / "prompts.db"
        build_database(sessions, md_paths, db_path)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT prompt_text FROM prompts_fts WHERE prompts_fts MATCH 'pytest'"
        ).fetchall()
        assert len(rows) == 1
        assert "pytest" in rows[0][0].lower()

    def test_idempotent_rebuild(self, tmp_history: Path, tmp_output: Path):
        """Running build_database twice produces the same result."""
        import sqlite3

        sessions = parse_history(tmp_history)
        md_paths = {sid: f"fake/{sid}.md" for sid in sessions}
        db_path = tmp_output / "prompts.db"

        build_database(sessions, md_paths, db_path)
        build_database(sessions, md_paths, db_path)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert count == 4
