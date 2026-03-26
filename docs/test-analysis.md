# Test Coverage Analysis

## Project: promptvault-py | Language: Python 3.10+ | Test Runner: pytest
## Existing Tests: 7 files (incl. conftest), 181 test functions, avg assertion density: 1.4

**Analysis date:** 2026-03-26
**Total tests:** 190 passing (pytest reports 190 due to parametrize expansion)
**Total assertions:** 252 across 181 test functions

---

## PRIORITY 1 -- Critical Untested Code (risk >= 15)

| # | File:Function | Risk | Why | Recommended Strategy |
|---|--------------|------|-----|---------------------|
| 1 | `search.py:_run_fzf` | **17** | Public-facing subprocess orchestrator (blast=5, complexity=5, change=3, data=4). Handles fzf subprocess, file opening with EDITOR, error handling for missing fzf. Only tested indirectly via mock in `test_search_coverage.py::TestCmdSearchInteractive::test_with_results_calls_run_fzf` which patches it away entirely. Zero coverage of: fzf return code handling, md_path extraction from stdout, EDITOR fallback, FileNotFoundError branch, ctrl-y copy binding, `--disabled` + reload binding when db_path is provided. | Unit test with mocked `subprocess.run`: verify correct fzf args, test return code 0 path (file open), test return code != 0 (no action), test FileNotFoundError, test db_path reload binding injection. |
| 2 | `search.py:get_db` | **16** | Entry gate for every CLI command (blast=5, complexity=3, change=3, data=5). Calls `_auto_sync_if_stale` then opens DB. If DB missing after sync, prints error and `sys.exit(1)`. Not directly unit tested -- only exercised implicitly through higher-level command tests that use `populated_env` fixture (which pre-builds the DB). The missing-DB-after-sync branch is never tested. | Test: (1) DB exists -> returns connection, (2) DB missing -> sys.exit(1) with correct stderr message, (3) auto-sync is called before DB check. |
| 3 | `search.py:main` (search entry point) | **15** | CLI dispatch hub (blast=5, complexity=5, change=3, data=2). Has 6 branches: `_fzf-lines` hidden command, 4 named subcommands, no-subcommand fallback (interactive vs plain). The `_fzf-lines` path is tested via subprocess in e2e. The no-subcommand interactive path (fzf + isatty) is untested. The global `--no-fzf` propagation is only partially tested. | Test: no-subcommand with `has_fzf()=True` + `isatty()=True` calls `cmd_search_interactive`. Test unknown subcommand falls through to else branch. |

## PRIORITY 2 -- Important Untested Code (risk 10-14)

| # | File:Function | Risk | Why | Recommended Strategy |
|---|--------------|------|-----|---------------------|
| 4 | `search.py:_fzf_preview_script` | **12** | Generates shell script for fzf preview (blast=3, complexity=3, change=3, data=3). Contains shell interpolation with `{q}` and path construction. Incorrect quoting could break preview or expose injection. | Unit test: verify returned string contains vault_dir path, `{q}`, sed/grep commands. Test with vault_dir containing spaces. |
| 5 | `search.py:_fzf_copy_script` | **12** | Generates shell script for ctrl-y copy (blast=3, complexity=3, change=3, data=3). Same shell injection risk as preview script. | Unit test: verify returned string contains vault_dir path, pbcopy command. |
| 6 | `search.py:cmd_recent` (fzf branch) | **12** | The fzf-interactive branch of `cmd_recent` (blast=3, complexity=4, change=3, data=2) is never tested. Only the `--no-fzf` plain branch is covered. The fzf branch builds conversation lines differently (includes `display_name` as 3rd tab field), which could silently break. | Mock `_run_fzf` and verify it receives correctly formatted lines with 3 tab-separated fields. |
| 7 | `search.py:cmd_list` (fzf branch) | **12** | Same as cmd_recent: the fzf-interactive branch is never tested. It builds lines identically to cmd_recent's fzf branch. | Mock `_run_fzf` and verify correct line format and header. |
| 8 | `search.py:ts_to_str` | **10** | Timestamp formatter (blast=3, complexity=1, change=1, data=5). Used in every search result display. Wrong formatting silently corrupts all output. Pure function, trivial to test. | Parametrized test: known epoch ms -> expected "YYYY-MM-DD HH:MM" string. |
| 9 | `search.py:ts_to_short` | **10** | Short timestamp formatter (blast=3, complexity=1, change=1, data=5). Used in conversation line builder. Same risk as ts_to_str. | Parametrized test: known epoch ms -> expected "MM-DD HH:MM" string. |
| 10 | `sync.py:_resolve_paste_content` | **10** | Recently added (commit 119c0d4) paste-cache resolver (blast=3, complexity=3, change=5, data=3). Tested **indirectly** through `resolve_pasted_content` tests (`test_hash_referenced_paste_resolved_from_cache`, `test_hash_referenced_paste_missing_cache_file`, `test_inline_content_preferred_over_hash`). However, not tested **directly** for: empty content string with whitespace-only, OSError on cache read, empty contentHash string. | Direct unit tests: (1) content=" " returns empty, (2) OSError on cache file read returns "", (3) contentHash="" returns "", (4) both content and contentHash missing returns "". |

## PRIORITY 3 -- Nice to Have (risk < 10)

| # | File:Function | Risk | Why |
|---|--------------|------|-----|
| 11 | `sync.py:ts_to_datetime` | 8 | Pure function, zero branches. Tested indirectly everywhere via `generate_markdown`, `generate_vault`, etc. Direct test would be trivial. |
| 12 | `search.py:has_fzf` | 6 | One-liner wrapping `shutil.which`. Tested indirectly via mocks. |
| 13 | `hook.py:main` edge cases | 7 | Missing fields in JSON input (e.g., no "prompt" key) -- defaults to empty string. The broad `except Exception: pass` swallows all errors. Currently 3 tests cover happy path, invalid input, and append-multiple. Could add: empty JSON object, missing PROMPTVAULT_CAPTURE_LOG env var (default path), permission error on log directory. |

---

## Existing Tests -- Quality Issues

| File | Issue | Severity |
|------|-------|----------|
| `test_search_coverage.py` | 4 test functions (`test_no_history_file_skips_sync`, `test_no_db_triggers_sync`, `test_history_newer_than_db_triggers_sync`, `test_db_newer_than_history_skips_sync`) contain **no `assert` statements**. They rely on `mock_sync.assert_not_called()` / `fake_sync.assert_called_once_with()` which are mock methods, not pytest assertions. If the mock name is misspelled (e.g., `assert_not_callled()`), the test silently passes. | **HIGH** -- should wrap mock calls in explicit `assert` or use `pytest-mock`'s `assert_called_once_with` which raises. |
| `test_search_coverage.py` | Overall assertion density: **1.1** (below 2.0 threshold). 29 tests with only 33 assertions. Many tests have a single assertion checking output contains a substring. | **MEDIUM** -- add secondary assertions to verify no error output (stderr), correct return codes, or negative checks. |
| `test_search.py` | Assertion density: **1.3** (below 2.0 threshold). 36 tests with 48 assertions. | **MEDIUM** -- some tests like `test_search_with_bm25_ranking` are well-structured (3 assertions); others like `test_prefix_search_single_char` have only `assert len(rows) >= 1`. |
| `test_e2e.py` | Assertion density: **1.3** (below 2.0 threshold). 51 tests with 64 assertions. | **LOW** -- acceptable for e2e tests which inherently test integration. Many tests correctly verify both positive and negative conditions. |
| `test_sync.py` | Assertion density: **1.6** (below 2.0 threshold). 29 tests with 47 assertions. | **LOW** -- close to threshold, reasonable for unit tests. |
| `test_search.py` | 1 weak assertion detected (`assert len(rows) >= 1` pattern used as sole assertion in several tests). | **LOW** -- these tests verify FTS search works, and the `>= 1` check is appropriate given variable result counts. |

---

## Recommended Test Infrastructure

### Already in good shape:
- `conftest.py` provides `tmp_history` and `tmp_output` fixtures
- `test_e2e.py` has a comprehensive `e2e_env` fixture with 11 sessions covering all edge cases
- `test_search_coverage.py` has `populated_env` fixture and `no_auto_sync` autouse fixture
- Tests use `monkeypatch` for env vars -- never touch real `~/.claude/`
- Parametrized tests used for `format_duration` boundaries

### Recommended additions:

1. **Fixture for mock fzf subprocess**: Create a `conftest.py` fixture that patches `subprocess.run` to simulate fzf output. This would unblock testing `_run_fzf`, `cmd_recent` fzf branch, and `cmd_list` fzf branch.

2. **Fixture for empty/minimal DB**: Reuse the pattern from `test_search_coverage.py::TestCmdRecent::test_empty_db_prints_message` but centralize it as a `@pytest.fixture` in conftest since it's duplicated in `TestCmdSearchInteractive::test_empty_db_no_query_prints_no_conversations`.

3. **Marker for slow tests**: The 3 subprocess-based hook tests and e2e `_fzf-lines` tests spawn Python processes. Add `@pytest.mark.slow` marker for CI optimization.

4. **Mock assertion safety**: Replace bare `fake_sync.assert_called_once_with(quiet=True)` with `assert fake_sync.call_count == 1` followed by `assert fake_sync.call_args == ((,), {"quiet": True})` to avoid the silent-pass-on-typo problem with mock assertion methods.

---

## Summary

| Category | Count |
|----------|-------|
| Source functions | 40 |
| Directly tested | 33 (82.5%) |
| Indirectly tested only | 4 (10%) |
| Untested | 3 (7.5%) |
| Critical gaps (risk >= 15) | 3 |
| Important gaps (risk 10-14) | 7 |
| Test quality issues | 6 |

**Highest-impact action:** Test `_run_fzf` and `get_db` -- these are the two highest-risk untested code paths that guard data integrity and user-facing behavior. The recently added `_resolve_paste_content` (commit `119c0d4`) has good indirect coverage through `resolve_pasted_content` tests but would benefit from direct edge-case testing (OSError, empty hash, whitespace-only content).
