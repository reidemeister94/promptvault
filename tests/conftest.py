"""Shared fixtures for promptvault tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# pytest uses this module-level variable to skip files during collection;
# pyproject.toml's [tool.pytest.ini_options] does NOT support collect_ignore.
collect_ignore = ["visual_test.py"]


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
