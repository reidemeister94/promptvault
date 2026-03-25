# Chronicle: CLI UI Audit & Self-Improvement Loop

**Date:** 2026-03-25
**Scope:** All CLI commands (`stats`, `list`, `recent`, `search`, no-subcommand fallback)
**Method:** Iterative loop — run command, evaluate output, fix code, reinstall, re-sync, repeat
**Iterations:** 4 rounds until output was clean

---

## State Before Audit

902 conversations, 2900 prompts across 26 projects in the SQLite database.
All commands functional but with significant display quality issues.

---

## Issues Found & Fixed

### 1. Stats bar chart not proportional

**Symptom:** All project bars rendered at the same length (40 blocks) regardless of count.
`min(cnt, 40)` capped every bar at 40 — a project with 287 conversations looked identical to one with 64.

**Fix:** Normalize bar width relative to the maximum count:
```python
max_cnt = top_projects[0][1]
bar_len = max(1, int(cnt / max_cnt * max_bar))
```

### 2. Conversation names were raw slugs

**Symptom:** List and recent output showed slugified names like `io-voglio-che-ti-automigliori-in-questo-progetto-quindi-che` — unreadable, especially for non-English prompts.

**Fix:** Added `display_name` column to the `conversations` table. `make_display_name()` returns the first real prompt text, capitalized and truncated to 80 chars, with image/paste markers stripped. The slug `name` is kept for file paths only.

**Schema change:** `conversations` table gained `display_name TEXT NOT NULL`. All queries updated to use `COALESCE(display_name, name)` for backward compatibility.

### 3. `[Image #N]` markers shown as-is

**Symptom:** Prompts containing only `[Image #14]` appeared verbatim in results. Multi-image prompts showed `[Image #1][Image #2][Image #3]`.

**Fix:** Two-layer approach:
- `clean_prompt_text()` strips image markers and shows `(image)` or `(images)` if nothing else remains.
- SQL filter in `recent` excludes image-only prompts: `WHERE p.prompt_text NOT GLOB '[[]Image #[0-9]*[]]'`

### 4. Slash commands leaking into prompt results

**Symptom:** `/plugin` and other custom slash commands appeared as prompts in `recent` output. The `is_slash_command()` regex `^/[a-z-]+$` didn't match `/plugin` because `-` was the only allowed special char and the pattern required end-of-string.

**Fix:** Broadened the regex to `^/[a-z][\w-]*$` to catch any `/word` pattern. Added SQL GLOB filter `WHERE p.prompt_text NOT GLOB '/[a-z]*'` in `recent` queries.

### 5. `--no-fzf` without subcommand showed help text

**Symptom:** Running `promptvault --no-fzf` in a non-TTY or without fzf installed printed `argparse` help instead of useful output.

**Fix:** Fallback now runs `cmd_recent` with `count=15` when no subcommand is given and fzf is unavailable.

### 6. Empty sessions cluttered the list

**Symptom:** Sessions with only slash commands (e.g., `/compact`, `/plugin`) appeared as `(no text prompts) 0p` — noise that pushed real conversations down.

**Fix:**
- `prompt_count` now stores 0 for slash-command-only sessions (previously fell back to counting all prompts including commands).
- All list/recent/browse queries filter `WHERE prompt_count > 0`.
- `stats` uses `SUM(prompt_count)` and filters empty sessions from counts.

### 7. Project names truncated poorly

**Symptom:** `dufercotp-shipping..` (middle-cut) was confusing. Previous `name[:18] + ".."` lost the meaningful suffix.

**Fix:** Simple end-truncation with ellipsis: `name[:19] + "…"` at max 20 chars. Middle-cut was tried and rejected — it produced strings like `dufercotp-..er-backend` that were harder to scan. User-specific prefix stripping (`dufercotp-`, `dtp-`) was also tried and reverted as not generalizable.

### 8. Secondary line in recent/search was noisy

**Symptom:** The line below each prompt showed `slug-name | project | 2026/03/2026-03-25__abc123__slug-name.md` — the full file path was redundant and hard to read.

**Fix:** Replaced with `display_name (truncated to 50 chars) · project_name`. The `·` separator is lighter than `|`. File path removed entirely from plain output (still accessible via fzf preview).

### 9. Pre-existing broken test

**Discovery:** `test_fts_search_works` searched for `'shipping'` but no test fixture contained that word. The test was already failing on `main` before any changes.

**Fix:** Changed search term to `'pytest'` which exists in the fixture data (`"explain how to use pytest fixtures"`).

---

## Decisions & Trade-offs

| Decision | Chosen | Rejected | Why |
|----------|--------|----------|-----|
| Conversation display name | New `display_name` DB column | Compute at display time | Avoids re-parsing on every query; one-time cost at sync |
| Project name truncation | Simple end-truncation with `…` | Middle-cut (`foo..bar`) | Middle-cut was harder to scan visually |
| Empty session handling | Filter in SQL queries | Don't store them at all | Keeping them in DB preserves completeness; filtering is cheap |
| Slash command regex | Broad `/[a-z][\w-]*` | Explicit allowlist only | Future-proof — new commands don't need manual addition |
| Image-only prompt filtering | SQL GLOB + display cleanup | Skip at sync time | Keeps raw data in DB for search; only hides from browsing |

---

## Metrics After Audit

| Metric | Before | After |
|--------|--------|-------|
| Conversations shown | 902 (including empty) | 630 (real prompts only) |
| Prompts counted | 2606 (including slash commands) | 2278 (real prompts only) |
| Test suite | 35 passed, 1 failed | 36 passed |

---

## Remaining Observations

These are minor items that could be addressed in future iterations:

- **Duplicate conversation titles**: Two consecutive sessions starting with the same prompt (e.g., "Scarica tutti i dati da...") show identical titles in the list. A disambiguation suffix (session time or ID fragment) could help.
- **`cd /Users/...` prompts from VSCode debug**: Some prompts are auto-generated debug commands pasted from the IDE. `_clean_for_title()` strips leading `cd` paths, but they still appear in search results.
- **fzf preview could highlight search terms**: Currently only highlights when a query is passed via CLI; fzf's own filter typing doesn't trigger grep highlighting.
