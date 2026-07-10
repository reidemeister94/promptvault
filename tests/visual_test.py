"""Visual testing tool for promptvault's fzf interface.

Uses pexpect to spawn pv in a pseudo-terminal and pyte to render the
terminal output into a queryable screen buffer. Provides assertion flags
for programmatic verification after UI changes.

Usage:
    python tests/visual_test.py                       # Default view
    python tests/visual_test.py --keys ctrl-t         # Send ctrl-t
    python tests/visual_test.py --query best-pr       # Type query into fzf
    python tests/visual_test.py --cols 80             # Narrow terminal

Assertion mode (programmatic verification):
    python tests/visual_test.py --query best-pr --assert-min 1
    python tests/visual_test.py --query best-pr --assert-no-text "File not found"
    python tests/visual_test.py --keys ctrl-t --assert-text "prompt>"
    python tests/visual_test.py --json                # Machine-readable output

Requirements: pexpect, pyte (pip install pexpect pyte)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import pexpect
import pyte

# Control character codes for fzf keybindings
KEY_MAP = {
    "ctrl-t": "\x14",
    "ctrl-p": "\x10",
    "ctrl-d": "\x04",
    "ctrl-g": "\x07",
    "ctrl-b": "\x02",
    "ctrl-s": "\x13",
    "ctrl-o": "\x0f",
    "ctrl-x": "\x18",
    "ctrl-y": "\x19",
    "ctrl-e": "\x05",
    "ctrl-/": "\x1f",
    "tab": "\t",
    "btab": "\x1b[Z",
    "enter": "\r",
    "esc": "\x1b",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "alt-r": "\x1br",
}


class FzfHarness:
    """Drive pv's fzf UI via pexpect + pyte for reliable screen capture."""

    def __init__(self, rows: int = 45, cols: int = 140):
        self.rows = rows
        self.cols = cols
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        pv_cmd = f"{sys.executable} -m promptvault.search"
        self.child = pexpect.spawn(
            "bash",
            ["-c", pv_cmd],
            dimensions=(rows, cols),
            encoding=None,  # bytes mode for pyte
            env={**os.environ, "TERM": "xterm-256color"},
        )

    def send(self, text: str):
        """Send raw text/keystrokes to fzf."""
        self.child.send(text.encode())

    def send_key(self, key_name: str):
        """Send a named key (e.g. 'ctrl-t', 'tab', 'enter')."""
        code = KEY_MAP.get(key_name, key_name)
        self.child.send(code.encode())

    def _read_output(self):
        """Read all available output from the PTY and feed to pyte.

        Handles DSR (Device Status Report): fzf sends ESC[6n to ask cursor
        position and hangs until the terminal replies with ESC[row;colR.
        """
        while True:
            try:
                data = self.child.read_nonblocking(size=65536, timeout=0.1)
                if data:
                    if b"\x1b[6n" in data:
                        row = self.screen.cursor.y + 1
                        col = self.screen.cursor.x + 1
                        self.child.send(f"\x1b[{row};{col}R".encode())
                    self.stream.feed(data)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def capture(self) -> list[str]:
        """Read PTY output and return screen lines from pyte."""
        self._read_output()
        lines = []
        for row in range(self.rows):
            line = "".join(self.screen.buffer[row][col].data for col in range(self.cols)).rstrip()
            lines.append(line)
        # Strip trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return lines

    def until(self, predicate, timeout: float = 10.0) -> list[str]:
        """Poll screen until predicate(lines) is truthy. Retry every 100ms."""
        start = time.time()
        last_lines: list[str] = []
        while True:
            last_lines = self.capture()
            try:
                if predicate(last_lines):
                    return last_lines
            except (AssertionError, IndexError, ValueError):
                pass
            if time.time() - start > timeout:
                screen_dump = "\n".join(last_lines)
                raise TimeoutError(
                    f"Visual test timeout after {timeout}s.\n"
                    f"Screen ({len(last_lines)} lines):\n{screen_dump}"
                )
            time.sleep(0.1)

    def close(self):
        """Send Escape and close the child process."""
        try:
            self.child.send(b"\x1b")
            time.sleep(0.3)
            self.child.close(force=True)
        except Exception:
            pass


def parse_fzf_state(lines: list[str]) -> dict:
    """Extract structured state from captured fzf screen lines."""
    state: dict = {
        "query": "",
        "result_count": -1,
        "total": -1,
        "matched": -1,
        "prompt_mode": "unknown",
        "has_preview": False,
        "preview_text": "",
        "footer_visible": False,
        "footer_lines": [],
        "screen_lines": lines,
        "raw_line_count": len(lines),
    }

    for line in lines:
        prompt_match = re.search(r"(conv|prompt)(?:\s*\[.*?\])?>\s*(.*)", line)
        if prompt_match:
            state["prompt_mode"] = prompt_match.group(1)
            # Strip fzf border chars from query
            state["query"] = prompt_match.group(2).strip().rstrip("│").strip()

        count_match = re.search(r"(\d+)/(\d+)\s*\((\d+)\)", line)
        if count_match:
            state["matched"] = int(count_match.group(1))
            state["result_count"] = int(count_match.group(2))
            state["total"] = int(count_match.group(2))

        conv_match = re.search(r"(\d+)\s+conversations", line)
        if conv_match:
            state["total"] = int(conv_match.group(1))

        if "^t mode" in line or "^o open" in line:
            state["footer_visible"] = True
            state["footer_lines"].append(line.strip())

        if "File not found" in line:
            state["has_preview"] = True
            state["preview_text"] = "File not found"
        elif line.strip().startswith("#") and ">" not in line:
            state["has_preview"] = True
            if not state["preview_text"]:
                state["preview_text"] = line.strip()

    return state


def run_assertions(state: dict, args: argparse.Namespace) -> list[str]:
    """Check assertions against parsed state. Returns list of failure messages."""
    failures = []

    if args.assert_min is not None:
        actual = state["matched"]
        if actual < args.assert_min:
            failures.append(f"FAIL: assert-min {args.assert_min}, got {actual} matched results")

    if args.assert_count is not None:
        actual = state["matched"]
        if actual != args.assert_count:
            failures.append(f"FAIL: assert-count {args.assert_count}, got {actual}")

    if args.assert_text:
        screen_text = "\n".join(state["screen_lines"])
        for text in args.assert_text:
            if text not in screen_text:
                failures.append(f"FAIL: assert-text '{text}' not found in screen")

    if args.assert_no_text:
        screen_text = "\n".join(state["screen_lines"])
        for text in args.assert_no_text:
            if text in screen_text:
                failures.append(f"FAIL: assert-no-text '{text}' WAS found in screen")

    return failures


def main():
    parser = argparse.ArgumentParser(description="Visual test tool for promptvault fzf UI")
    parser.add_argument("--query", help="Search query to type into fzf")
    parser.add_argument("--keys", nargs="+", help="Keys to send (e.g., ctrl-t tab)")
    parser.add_argument("--wait", type=int, default=3000, help="Ms to wait for results")
    parser.add_argument("--rows", type=int, default=45, help="Terminal rows")
    parser.add_argument("--cols", type=int, default=140, help="Terminal cols")

    # Assertion flags
    parser.add_argument("--assert-min", type=int, default=None, help="Assert at least N results")
    parser.add_argument("--assert-count", type=int, default=None, help="Assert exactly N results")
    parser.add_argument(
        "--assert-text", action="append", default=None, help="Assert text appears in screen"
    )
    parser.add_argument(
        "--assert-no-text",
        action="append",
        default=None,
        help="Assert text does NOT appear in screen",
    )
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()

    has_assertions = any(
        [
            args.assert_min is not None,
            args.assert_count is not None,
            args.assert_text,
            args.assert_no_text,
        ]
    )

    if not has_assertions and not args.json:
        print(f"Launching pv (wait={args.wait}ms, keys={args.keys})...", file=sys.stderr)

    wait_sec = args.wait / 1000.0
    harness = FzfHarness(rows=args.rows, cols=args.cols)

    try:
        # Wait for fzf to initialize (sync + render)
        harness.until(
            lambda lines: any("conv>" in ln or "prompt>" in ln for ln in lines),
            timeout=wait_sec + 10,
        )

        # Type query if provided
        if args.query:
            harness.send(args.query)
            # Wait for fzf reload to complete
            time.sleep(min(wait_sec, 3.0))

        # Send additional keys
        if args.keys:
            for key in args.keys:
                harness.send_key(key)
                time.sleep(0.5)

        # Final capture
        time.sleep(0.5)
        lines = harness.capture()

    finally:
        harness.close()

    state = parse_fzf_state(lines)
    screen_text = "\n".join(lines)

    if args.json:
        output = {k: v for k, v in state.items() if k != "screen_lines"}
        output["screen_excerpt"] = screen_text[:500]
        print(json.dumps(output, indent=2))

    if has_assertions:
        failures = run_assertions(state, args)
        if failures:
            for f in failures:
                print(f, file=sys.stderr)
            if not args.json:
                print("\n--- Screen capture ---", file=sys.stderr)
                print(screen_text[:2000], file=sys.stderr)
            sys.exit(1)
        else:
            if not args.json:
                print("ALL ASSERTIONS PASSED", file=sys.stderr)
            sys.exit(0)

    if not args.json:
        print(screen_text)

    # Save for inspection
    out_path = "/tmp/pv_visual_test.txt"
    with open(out_path, "w") as f:
        f.write(screen_text)
    print(f"\nSaved to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
