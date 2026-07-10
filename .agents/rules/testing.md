---
paths:
  - "tests/**"
  - "promptvault/search.py"
---

# Testing

`python -m pytest tests/ -v` — 369 tests, synthetic data only, never touch real `~/.claude/` or `~/.codex/` (autouse `_isolate_codex` fixture pins `PROMPTVAULT_CODEX_SESSIONS` to a nonexistent path).

- `conftest.py`: `tmp_history`, `tmp_output` fixtures (synthetic history.jsonl).
- `test_e2e.py`: `e2e_env` fixture (11 sessions, full vault+DB, covers all edge cases).

## Visual E2E (MANDATORY after any fzf UI change)

`pexpect` + `pyte` drive fzf in a real PTY and capture screen state (`pip install pexpect pyte`). The DSR handler in `FzfHarness._read_output()` is critical — fzf sends `ESC[6n` and hangs without a response.

```
PY=python  # env path in .claude/CLAUDE.md
$PY tests/visual_test.py --wait 3000 --assert-min 1                        # default view has results
$PY tests/visual_test.py --query "best-pr" --wait 3000 --assert-min 1      # hyphen query
$PY tests/visual_test.py --query "/best-pr" --wait 3000 --assert-min 1     # slash query
$PY tests/visual_test.py --keys ctrl-t --wait 3000 --assert-text "prompt>" # mode toggle
$PY tests/visual_test.py --cols 80 --wait 3000 --assert-text "^t mode"     # footer at 80 cols
```

Assertions: `--assert-min N`, `--assert-count N`, `--assert-text "str"`, `--assert-no-text "str"`. All must exit 0.
