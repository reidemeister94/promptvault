"""Shared fixtures for promptvault tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# pytest uses this module-level variable to skip files during collection;
# pyproject.toml's [tool.pytest.ini_options] does NOT support collect_ignore.
collect_ignore = ["visual_test.py"]


@pytest.fixture(autouse=True)
def _isolate_codex(monkeypatch, tmp_path):
    """Pin Codex sessions to a nonexistent path so no test reads the real ~/.codex.

    Tests that exercise Codex ingestion override this with their own fixture path.
    """
    monkeypatch.setenv("PROMPTVAULT_CODEX_SESSIONS", str(tmp_path / "no-codex"))


@pytest.fixture
def tmp_history(tmp_path: Path) -> Path:
    """Create a synthetic history.jsonl file for testing."""
    history_path = tmp_path / "history.jsonl"
    entries = [
        {
            "display": "/help ",
            "pastedContents": {},
            "timestamp": 1700000000000,
            "project": "/Users/test/project-a",
            "sessionId": "aaaa-1111-2222-3333",
        },
        {
            "display": "explain how to use pytest fixtures",
            "pastedContents": {},
            "timestamp": 1700000060000,
            "project": "/Users/test/project-a",
            "sessionId": "aaaa-1111-2222-3333",
        },
        {
            "display": "can you add type hints to the function?",
            "pastedContents": {},
            "timestamp": 1700000120000,
            "project": "/Users/test/project-a",
            "sessionId": "aaaa-1111-2222-3333",
        },
        {
            "display": "fix the authentication bug in the API endpoint",
            "pastedContents": {},
            "timestamp": 1700100000000,
            "project": "/Users/test/project-b",
            "sessionId": "bbbb-4444-5555-6666",
        },
        {
            "display": "add a database migration for the new column",
            "pastedContents": {},
            "timestamp": 1700100300000,
            "project": "/Users/test/project-b",
            "sessionId": "bbbb-4444-5555-6666",
        },
        {
            "display": "/compact ",
            "pastedContents": {},
            "timestamp": 1700200000000,
            "project": "/Users/test/project-a",
            "sessionId": "cccc-7777-8888-9999",
        },
        {
            "display": "[Pasted text #1 +3 lines]\n\nanalyze the pasted code",
            "pastedContents": {
                "1": {
                    "id": 1,
                    "type": "text",
                    "content": "def hello():\n    print('world')\n    return True",
                }
            },
            "timestamp": 1700300000000,
            "project": "/Users/test/project-a",
            "sessionId": "dddd-0000-1111-2222",
        },
    ]
    with open(history_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return history_path


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Create a temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


def _rollout(session_id: str, cwd: str, records: list[dict]) -> str:
    """Serialize a synthetic Codex rollout file: session_meta line + records."""
    lines = [
        json.dumps(
            {
                "timestamp": "2026-07-09T09:53:16.000Z",
                "type": "session_meta",
                "payload": {"session_id": session_id, "cwd": cwd},
            }
        )
    ]
    lines += [json.dumps(r) for r in records]
    return "\n".join(lines) + "\n"


def _user_msg(text: str, iso: str) -> dict:
    """A Codex event_msg/user_message record (the clean prompt channel)."""
    return {
        "timestamp": iso,
        "type": "event_msg",
        "payload": {"type": "user_message", "message": text},
    }


def _noise(iso: str) -> dict:
    """A non-prompt record (developer blob) that must be ignored."""
    return {
        "timestamp": iso,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "# env"}],
        },
    }


@pytest.fixture
def tmp_codex_sessions(tmp_path: Path) -> Path:
    """Synthetic ~/.codex/sessions tree with rollout files covering edge cases."""
    sessions = tmp_path / "codex-sessions"
    day = sessions / "2026" / "07" / "09"
    day.mkdir(parents=True)

    # File A: 2 prompts + a consecutive duplicate (dedup) + noise, out of order.
    file_a = day / "rollout-2026-07-09T09-52-46-codex-sess-1111.jsonl"
    file_a.write_text(
        _rollout(
            "codex-sess-1111",
            "/Users/test/project-c",
            [
                _noise("2026-07-09T09:53:16.700Z"),
                _user_msg("refactor the parser for codex", "2026-07-09T09:53:20.000Z"),
                _user_msg("refactor the parser for codex", "2026-07-09T09:53:21.000Z"),  # dup
                _user_msg("now add a test for it", "2026-07-09T09:54:00.000Z"),
            ],
        )
    )
    # A malformed line appended — must be skipped, file still parses.
    with open(file_a, "a") as f:
        f.write("{not valid json\n")

    # File B: 1 prompt, same project.
    file_b = day / "rollout-2026-07-09T10-00-00-codex-sess-2222.jsonl"
    file_b.write_text(
        _rollout(
            "codex-sess-2222",
            "/Users/test/project-c",
            [_user_msg("what does uv.lock do", "2026-07-09T10:00:05.000Z")],
        )
    )

    # File C: session_meta but no user_message → yields no conversation.
    file_c = day / "rollout-2026-07-09T11-00-00-codex-empty.jsonl"
    file_c.write_text(
        _rollout("codex-empty", "/Users/test/project-c", [_noise("2026-07-09T11:00:01.000Z")])
    )

    # File D: no session_meta at all → skipped entirely.
    file_d = day / "rollout-2026-07-09T12-00-00-codex-nometa.jsonl"
    file_d.write_text(json.dumps(_user_msg("orphan prompt", "2026-07-09T12:00:00.000Z")) + "\n")

    return sessions
