# Test Coverage Analysis

## Project: promptvault | Language: Python 3.10+ | Test Runner: pytest
## Existing Tests: 5 files, 116 test functions, avg assertion density: 1.6

---

## PRIORITY 1 — Critical Untested Code (risk >= 15)

| # | File:Function | Risk | Why | Recommended Strategy |
|---|--------------|------|-----|---------------------|
| 1 | `search.py:_auto_sync_if_stale` | 17 | **Blast 5** (entry point, triggers full sync pipeline), **Complexity 4** (file stat comparison, conditional import + execution), **Change 3** (recent changes), **Data 5** (data integrity — stale DB means wrong search results). Silent failure could serve stale data indefinitely. | Unit test with mocked file stats: DB newer than history (no sync), history newer (sync triggered), DB missing (sync triggered). Verify sync_main is called exactly when expected. |
| 2 | `search.py:cmd_list` | 16 | **Blast 5** (public CLI command, user-facing), **Complexity 4** (date parsing, project filter, SQL construction with dynamic WHERE clauses, fzf/plain branching), **Change 3** (recent), **Data 4** (business logic — filters user's conversation data). SQL injection risk from string interpolation in conditions. | Test with: date filter valid/invalid, project filter partial match, limit param, no-fzf plain output. Use capsys to capture output. |
| 3 | `search.py:cmd_recent` | 15 | **Blast 5** (public CLI command, default fallback when no subcommand), **Complexity 3** (fzf/plain branching, SQL with GLOB patterns), **Change 3** (recent), **Data 4** (business logic). This is the default command users see. | Test plain mode output: verify ordering (most recent first), slash commands excluded, correct count param. |
| 4 | `sync.py:main` | 15 | **Blast 5** (entry point for `promptvault-sync` CLI), **Complexity 4** (orchestrates parse + vault + index + DB, file I/O, env vars, shutil.rmtree), **Change 3** (recent), **Data 3** (orchestration). Destructive: calls `shutil.rmtree` on vault dir. | Integration test with tmp dirs and env vars. Verify: vault created, DB created, index created. Test missing history file path (exit 1). Test quiet mode. |
| 5 | `search.py:main` | 15 | **Blast 5** (CLI entry point for `promptvault`), **Complexity 3** (argparse dispatch, fzf detection, fallback logic), **Change 3** (recent), **Data 4** (user-facing). Unknown subcommand or no subcommand triggers fallback behavior. | Test CLI dispatch: `promptvault search`, `promptvault stats`, `promptvault` (no subcommand). Mock sys.argv and fzf availability. |

## PRIORITY 2 — Important Untested Code (risk 10-14)

| # | File:Function | Risk | Why | Recommended Strategy |
|---|--------------|------|-----|---------------------|
| 6 | `sync.py:generate_markdown` | 14 | **Blast 3** (generates vault files), **Complexity 4** (frontmatter generation, timestamp formatting, whitespace cleaning, regex substitution), **Change 3**, **Data 4** (data transformation — prompt text integrity). Tested indirectly via `generate_vault` but no direct assertion on markdown structure. | Direct tests: verify frontmatter fields (session_id, project, dates), prompt section structure, whitespace stripping, slash command filtering in prompt list. |
| 7 | `sync.py:make_display_name` | 13 | **Blast 3** (displayed in search results and DB), **Complexity 3** (summary preference, fallback chain, title cleaning, truncation), **Change 3**, **Data 4** (user-visible naming). Indirectly tested via e2e DB assertions but no unit test for edge cases. | Unit tests: summary provided (use it), no summary (use first prompt), all slash commands (return "(no text prompts)"), prompt with markers only, 80+ char truncation. |
| 8 | `sync.py:format_duration` | 10 | **Blast 2** (display only), **Complexity 2** (simple arithmetic with thresholds), **Change 2**, **Data 4** (displayed in markdown). Three branches: <1 min, <60 min, 1h+. | Unit tests: 0 seconds ("<1 min"), 30 minutes, 90 minutes ("1h 30m"), exactly 60 minutes. |
| 9 | `sync.py:_clean_for_title` | 10 | **Blast 2** (internal helper for display), **Complexity 3** (multiple regex patterns for image/paste/cd markers), **Change 2**, **Data 3**. | Unit tests: image markers removed, paste markers removed, cd/env prefix removed, combined markers, clean text unchanged. |
| 10 | `search.py:_fts_session_ids` | 12 | **Blast 4** (powers all FTS-filtered conversation views), **Complexity 3** (FTS query + OR fallback + error handling), **Change 3**, **Data 2**. Tested indirectly via `_build_conversation_lines` but OR fallback path not verified. | Test: single word match, multi-word AND match, multi-word OR fallback (when AND returns nothing), invalid FTS syntax (OperationalError caught). |
| 11 | `search.py:cmd_search` | 11 | **Blast 4** (dispatch for search subcommand), **Complexity 2** (fzf/plain branching), **Change 3**, **Data 2**. | Test with args: no-fzf flag, query provided, no query + no fzf. |
| 12 | `search.py:cmd_search_interactive` | 10 | **Blast 3**, **Complexity 2** (thin wrapper), **Change 3**, **Data 2**. Hard to test (requires fzf), low standalone logic. | Mock `_run_fzf` and verify it's called with correct args. Test empty result message. |

## PRIORITY 3 — Nice to Have (risk < 10)

- `sync.py:ts_to_datetime` (risk 6) — pure function, trivial, indirectly tested via markdown generation
- `search.py:ts_to_str` / `ts_to_short` (risk 6) — pure display formatters, indirectly tested
- `search.py:has_fzf` (risk 5) — thin wrapper around `shutil.which`
- `search.py:_fzf_preview_script` / `_fzf_copy_script` (risk 4) — shell string builders, hard to unit test meaningfully
- `search.py:_run_fzf` (risk 7) — subprocess orchestration with fzf, requires integration test environment

## Existing Tests — Quality Issues

| File | Issue | Severity |
|------|-------|----------|
| `tests/test_search.py` | Assertion density 1.3, weak assertion ratio 0.20. 9 weak assertions (e.g., `assert len(rows) >= 1` as sole check — proves existence but not correctness). | Medium |
| `tests/test_e2e.py` | Assertion density 1.3, weak assertion ratio 0.19. 12 weak assertions. Several tests assert `len(lines) >= 1` without verifying content. | Medium |
| `tests/test_sync.py` | Assertion density 1.6 (borderline). Tests like `test_creates_markdown_files` check file existence but not content structure beyond frontmatter presence. | Low |
| `tests/test_e2e.py:test_recent_plain` | Does not actually call `cmd_recent` — manually runs SQL instead. Tests the query, not the command function. | High |
| `tests/test_e2e.py:test_display_name_fallback_when_no_summary` | Assertion `"check these screenshots" in row[0].lower() or "image" not in row[0].lower()` — OR-condition always passes if second clause is true, making the test vacuous. | High |
| `tests/test_search.py:test_search_with_bm25_ranking` | Asserts `len(rows) >= 1` but does not verify ranking order (bm25 scores are selected but never checked). | Medium |

## Recommended Test Infrastructure

The project already has a solid `conftest.py` with `tmp_history` and `tmp_output` fixtures, plus a rich `e2e_env` fixture. Recommendations:

1. **Add a `populated_db` fixture to conftest.py** — the `db_path` fixture in `test_search.py` and the `e2e_env` fixture in `test_e2e.py` both build databases from scratch. Extract a shared fixture to reduce duplication.

2. **Add a `mock_env` fixture** for testing `sync.main` and `search.main` — these depend on environment variables (`PROMPTVAULT_HISTORY`, `PROMPTVAULT_OUTPUT`, etc.). A fixture that sets and restores env vars cleanly would enable proper integration tests.

3. **Add `@pytest.mark.slow` marker** — the e2e tests involve subprocess calls and full DB rebuilds. Marking them allows `pytest -m "not slow"` for fast feedback loops.

4. **Consider property-based testing** for `slugify`, `format_duration`, and `_clean_for_title` — these are pure functions with well-defined input/output contracts, ideal for Hypothesis.
