"""Tests for promptvault.hook module — in-process for coverage."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path


class TestHookInProcess:
    """Call hook.main() in-process so coverage tracks the lines."""

    def test_appends_to_capture_log(self, tmp_path, monkeypatch):
        log_path = tmp_path / "capture.jsonl"
        monkeypatch.setenv("PROMPTVAULT_CAPTURE_LOG", str(log_path))

        input_data = json.dumps(
            {
                "session_id": "test-session-123",
                "prompt": "explain this code",
                "cwd": "/Users/test",
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(input_data))

        from promptvault.hook import main

        main()

        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["prompt"] == "explain this code"
        assert entry["session_id"] == "test-session-123"
        assert entry["cwd"] == "/Users/test"
        assert isinstance(entry["timestamp"], int)
        assert entry["timestamp"] > 0

    def test_silent_on_invalid_json(self, tmp_path, monkeypatch):
        log_path = tmp_path / "capture.jsonl"
        monkeypatch.setenv("PROMPTVAULT_CAPTURE_LOG", str(log_path))
        monkeypatch.setattr("sys.stdin", io.StringIO("not valid json"))

        from promptvault.hook import main

        # Must not raise — the except Exception: pass catches it
        main()
        assert not log_path.exists()

    def test_appends_multiple_entries(self, tmp_path, monkeypatch):
        log_path = tmp_path / "capture.jsonl"
        monkeypatch.setenv("PROMPTVAULT_CAPTURE_LOG", str(log_path))

        from promptvault.hook import main

        for i in range(3):
            monkeypatch.setattr(
                "sys.stdin",
                io.StringIO(
                    json.dumps({"prompt": f"prompt {i}", "session_id": f"s{i}", "cwd": "/"})
                ),
            )
            main()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["prompt"] == f"prompt {i}"
            assert entry["session_id"] == f"s{i}"

    def test_missing_fields_default_to_empty(self, tmp_path, monkeypatch):
        log_path = tmp_path / "capture.jsonl"
        monkeypatch.setenv("PROMPTVAULT_CAPTURE_LOG", str(log_path))
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

        from promptvault.hook import main

        main()

        entry = json.loads(log_path.read_text().strip())
        assert entry["prompt"] == ""
        assert entry["session_id"] == ""
        assert entry["cwd"] == ""

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        log_path = tmp_path / "nested" / "deep" / "capture.jsonl"
        monkeypatch.setenv("PROMPTVAULT_CAPTURE_LOG", str(log_path))
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(json.dumps({"prompt": "hello"})),
        )

        from promptvault.hook import main

        main()

        assert log_path.exists()
        assert "hello" in log_path.read_text()


class TestHookSubprocess:
    """Subprocess tests verify the script works as a standalone executable."""

    def test_end_to_end(self, tmp_path):
        log_path = tmp_path / "capture.jsonl"
        hook_script = Path(__file__).parent.parent / "promptvault" / "hook.py"

        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=json.dumps({"prompt": "e2e test", "session_id": "s1", "cwd": "/tmp"}),
            capture_output=True,
            text=True,
            env={**os.environ, "PROMPTVAULT_CAPTURE_LOG": str(log_path)},
        )

        assert result.returncode == 0
        assert result.stdout == ""
        entry = json.loads(log_path.read_text().strip())
        assert entry["prompt"] == "e2e test"
