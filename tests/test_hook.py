"""Tests for promptvault.hook module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


class TestHookScript:
    def test_appends_to_capture_log(self, tmp_path: Path):
        """Hook should append a JSON line to capture.jsonl."""
        log_path = tmp_path / "capture.jsonl"
        hook_script = Path(__file__).parent.parent / "promptvault" / "hook.py"

        input_data = json.dumps(
            {
                "session_id": "test-session-123",
                "transcript_path": "/tmp/transcript",
                "cwd": "/Users/test",
                "permission_mode": "default",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "explain this code",
            }
        )

        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=input_data,
            capture_output=True,
            text=True,
            env={**os.environ, "PROMPTVAULT_CAPTURE_LOG": str(log_path)},
        )

        assert result.returncode == 0
        assert result.stdout == ""  # must be silent

        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["prompt"] == "explain this code"
        assert entry["session_id"] == "test-session-123"
        assert entry["cwd"] == "/Users/test"
        assert "timestamp" in entry

    def test_silent_on_invalid_input(self, tmp_path: Path):
        """Hook should not crash or produce output on invalid input."""
        hook_script = Path(__file__).parent.parent / "promptvault" / "hook.py"

        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input="not valid json",
            capture_output=True,
            text=True,
            env={**os.environ, "PROMPTVAULT_CAPTURE_LOG": str(tmp_path / "capture.jsonl")},
        )

        assert result.returncode == 0
        assert result.stdout == ""

    def test_appends_multiple_entries(self, tmp_path: Path):
        """Hook should append (not overwrite) on multiple calls."""
        log_path = tmp_path / "capture.jsonl"
        hook_script = Path(__file__).parent.parent / "promptvault" / "hook.py"

        for i in range(3):
            input_data = json.dumps(
                {
                    "session_id": f"session-{i}",
                    "prompt": f"prompt number {i}",
                    "cwd": "/tmp",
                    "hook_event_name": "UserPromptSubmit",
                }
            )
            subprocess.run(
                [sys.executable, str(hook_script)],
                input=input_data,
                capture_output=True,
                text=True,
                env={**os.environ, "PROMPTVAULT_CAPTURE_LOG": str(log_path)},
            )

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3
