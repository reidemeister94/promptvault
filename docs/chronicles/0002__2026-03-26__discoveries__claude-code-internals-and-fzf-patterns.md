# Chronicle: Non-obvious Discoveries — Claude Code Internals & fzf Patterns

**Date:** 2026-03-26
**Scope:** Data sources, fzf integration, search architecture
**Context:** Found during iterative self-improvement of the promptvault CLI

---

## Discovery 1: `sessions-index.json` — Claude Code's auto-generated session titles

**What:** Claude Code maintains `sessions-index.json` files inside `~/.claude/projects/<encoded-path>/` that contain auto-generated conversation summaries (titles).

**Schema:**
```json
{
  "version": 1,
  "entries": [
    {
      "sessionId": "uuid",
      "fullPath": "/absolute/path/to/session.jsonl",
      "fileMtime": 1768986774383,
      "firstPrompt": "the first user message",
      "summary": "Docker Networking and Compose Setup",
      "messageCount": 12,
      "created": "2026-01-20T11:01:37.211Z",
      "modified": "2026-01-20T11:05:19.854Z",
      "gitBranch": "dev",
      "projectPath": "/Users/.../project"
    }
  ]
}
```

**The `summary` field** is exactly the short title shown by `claude --resume`. It's 3-6 words, well-formatted, and human-readable. Examples:
- `"Claude API Usage Billing Explanation"`
- `"Plugin Installation and Marketplace Setup"`
- `"PDF Chunking & Parallel LLM Processing Refactor"`

**Reliability caveat:** On this machine, only 132 out of 902 sessions have summaries. Coverage is limited to 9 out of 23 projects, and only sessions from January–early February 2026 are indexed. The file appears to have been introduced in a specific Claude Code version and may not be maintained consistently. **Always implement a fallback** — the summary is a bonus, not a guarantee.

**How we use it:** `load_session_summaries()` in `sync.py` reads all `sessions-index.json` files and returns `{session_id: summary}`. `make_display_name()` uses the summary when available, otherwise generates a title from the first prompt.

---

## Discovery 2: `history.jsonl` prompts contain excessive trailing whitespace

**What:** Claude Code's terminal rendering adds trailing spaces/tabs to prompt text stored in `history.jsonl`. A single line of user text can have 50+ trailing spaces.

**Impact:** When stored in markdown files, these invisible spaces cause fzf preview wrapping artifacts — the preview shows `↵` characters and fake blank lines because the whitespace wraps to the next line.

**Example (raw):**
```
scarica tutti i dati da:           ·····················
  - ufficial doc claude code       ···················································
```
(dots represent actual space characters)

**Fix:** Two-layer defense:
1. `generate_markdown()` strips trailing whitespace per line: `re.sub(r"[^\S\n]+$", "", text, flags=re.MULTILINE)`
2. fzf preview pipes through `cat -s` as safety net

This also applies to the markdown title line (`# Title`) — the title is built from the first prompt which may contain trailing whitespace. Fix: `re.sub(r"\s+", " ", text).strip()` before truncation.

---

## Discovery 3: FTS5 requires explicit prefix wildcards for partial word matching

**What:** SQLite FTS5's `MATCH` operator does **not** do substring matching by default. Searching for `"superpo"` will NOT match `"superpowers"`. You must append `*` for prefix matching: `"superpo*"`.

**Impact:** Critical for interactive search — the user types incrementally in fzf, each keystroke is a partial word. Without `*`, the search returns 0 results until the user types a complete word that exists in the index.

**Fix:** `_fts_prepare_query()` appends `*` to the last word:
```python
def _fts_prepare_query(query: str) -> str:
    words = query.strip().split()
    if not words:
        return query
    words[-1] = words[-1] + "*"
    return " ".join(words)
```

Only the last word gets the wildcard because earlier words are presumably complete (user already moved past them with a space).

---

## Discovery 4: fzf's `--with-nth` changes what is searchable

**What:** fzf's `--with-nth=N` is documented as "transform the presentation". In practice, it also transforms what fzf's built-in filter searches. If you display only field 2 via `--with-nth=2`, fzf will NOT search hidden fields.

**Impact:** We initially tried to hide the full conversation title in a third tab-separated field for search, while showing only the short title. fzf ignored the hidden field entirely — typing any search term returned 0 results.

**Correct architecture:** Don't use fzf's built-in filtering for database-backed search. Instead:
1. Use `--disabled` to turn off fzf's filtering
2. Use `--bind 'change:reload(command {q})'` to re-query the database on each keystroke
3. The reload command (`promptvault _fzf-lines {q}`) runs FTS5 search and outputs new lines

This pattern gives us:
- Real full-text search across ALL prompts in ALL conversations (not just titles)
- Prefix matching via FTS5 wildcards
- Stable left-panel layout (titles never scroll horizontally)
- Sub-100ms response time (SQLite FTS5 is fast)

---

## Discovery 5: fzf preview `{q}` enables live keyword highlighting

**What:** fzf replaces `{q}` in the `--preview` command with the current query text in real-time. This means the preview can highlight search terms as the user types.

**Previous behavior:** Highlighting only worked when a query was passed via CLI argument (`promptvault search "docker"`). Typing in fzf's search box didn't highlight because the preview script was static.

**Fix:**
```shell
q={q};
if [ -n "$q" ]; then
    sed ... | GREP_COLOR='1;33' grep --color=always -i -E "$q|$"
else
    sed ...
fi
```

The `"$q|$"` pattern in grep highlights the search term while still showing all lines (the `|$` matches every line end, ensuring nothing is filtered out).

---

## Discovery 6: Blank line squeezing in markdown requires two separate fixes

**What:** Consecutive blank lines in prompts come from two distinct sources:
1. **Real blank lines** in the user's prompt text (e.g., between paragraphs)
2. **Trailing whitespace** that, after line wrapping, creates visual blank lines

**Fix 1** (blank lines): `re.sub(r"\n{3,}", "\n\n", text)` — collapses 3+ newlines to 2.

**Fix 2** (trailing whitespace): `re.sub(r"[^\S\n]+$", "", text, flags=re.MULTILINE)` — strips trailing spaces per line WITHOUT removing newlines.

The order matters: strip whitespace first, THEN squeeze blank lines. Otherwise, lines like `"text   \n   \n   \ntext"` (spaces + newlines) won't be caught by the `\n{3,}` pattern because the spaces break the sequence.

---

## Summary of architecture decisions

| Problem | Initial approach | Why it failed | Final solution |
|---------|-----------------|---------------|----------------|
| fzf search on conversations | fzf built-in filter on short titles | Can't search prompt content, only titles | `--disabled` + `change:reload()` with SQLite FTS |
| Partial word matching | FTS MATCH with raw query | "superpo" doesn't match "superpowers" | Append `*` to last word in query |
| Conversation titles | Full first prompt (80 chars) | Too long, causes horizontal scroll in fzf | Short title (4 words) in fzf, full title in plain mode |
| Display name source | Generate from first prompt | Generic, duplicates | Prefer Claude's `sessions-index.json` summary when available |
| Trailing whitespace | Not handled | Preview showed `↵` artifacts | Strip per-line in markdown generation |
