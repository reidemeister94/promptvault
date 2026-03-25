# Implementation Plan: Claude Code Prompt Library

## WORKFLOW STATE
Status: In Progress
Current Phase: 2 (Plan -- approach approved in brainstorming, pending implementation plan approval)
Phases remaining: 3, 4, 5, 6, 7
Research: docs/plans/0001__2026-03-25__research__claude-prompt-library.md
Chronicle: TBD -- decided in Phase 3
Verification: TBD

## Brainstorming Summary

**Task:** Build a local prompt library system that captures all Claude Code user prompts into Obsidian-compatible markdown files + SQLite with FTS5, searchable from terminal, zero external dependencies.

**Understanding:**
- **WHAT:** (1) UserPromptSubmit hook for real-time capture, (2) sync script to generate markdown vault + SQLite DB from history.jsonl, (3) terminal search CLI
- **WHY:** Build a searchable personal knowledge base of all prompts sent to Claude Code, enabling prompt reuse, pattern discovery, and conversation recall

**Approaches considered:**
1. **Sync-only from history.jsonl** -- Parse existing history file, generate markdown + SQLite | Complexity: LOW | Risk: No real-time capture, relies on periodic runs
2. **Hook-only (UserPromptSubmit)** -- Capture prompts at submission time, write markdown + SQLite directly | Complexity: MEDIUM | Risk: Loses 2999 existing prompts, hook failures = data loss
3. **Hybrid: hook capture + sync script** -- Hook writes to append-only log for real-time; sync script reads history.jsonl (authoritative) + hook log, generates markdown vault + SQLite | Complexity: MEDIUM | Risk: Two data paths to reconcile

**Recommended: Hybrid (hook capture + sync script)**
The sync script uses `~/.claude/history.jsonl` as the single source of truth -- it already contains 2999 prompts across 894 sessions with timestamps, session IDs, and project paths. The hook provides a lightweight real-time capture for use cases like "search my last 5 prompts" without running a full sync. The sync script is idempotent -- it always regenerates from history.jsonl, so hook failures have zero impact on data integrity.

**Evaluation verdict:** PROCEED
history.jsonl is a verified, authoritative source. FTS5 is confirmed available. The hybrid approach adds real-time capability without risking data integrity.

**Complexity:** MEDIUM | **Risk:** Low -- all data sources verified, no external dependencies

**Key risks identified:**
- history.jsonl format could change in future Claude Code updates (mitigated: schema is simple, easy to adapt)
- Large history could slow sync (mitigated: 2999 lines is trivial; even 100K would take <2 seconds)
- Conversation naming from first prompt may produce poor names for short/vague prompts (mitigated: truncation + fallback to session ID prefix)

## Approach Decision

**Selected:** Hybrid (hook capture + sync script)
**Project name:** promptvault
**Project location:** /Users/silvio.pavanetto/Documents/personal/promptvault
**User modifications:** All code lives in the new project directory (publishable on GitHub), not in ~/.claude/prompt-library/. The hook and sync scripts are installed from the project. Shell aliases point to the project scripts.
**Confirmed:** 2026-03-25

## Critical Analysis

### Complexity Score: 5/10

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| Reversibility | 0 | Fully reversible -- generates files, can delete and regenerate |
| Blast radius | 1 | New files only, touches one existing config (hooks.json) |
| Ambiguity | 2 | Multiple valid approaches for naming, organization, dedup |
| Novelty | 1 | Standard patterns (JSONL parsing, FTS5, markdown generation) |
| Stakes | 1 | Low -- personal tool, no production impact |

**Decision threshold: 4-5 = SKIP full analysis.** However, given the multi-component design, a LIGHT analysis is warranted.

### Key Risk
**Dual-write complexity.** The hook writes to `capture.jsonl` and the sync reads `history.jsonl`. If history.jsonl is the authoritative source for sync, the hook capture is redundant for anything the sync covers. The hook's value is only for real-time queries between syncs.
**Evidence:** This is a known pattern -- write-ahead log + periodic materialization. Well-understood, low risk.

### Watch Out For
- Do NOT try to merge/reconcile hook data with history.jsonl data. history.jsonl is always authoritative. The hook log is a convenience cache only.
- Do NOT add prompt transformation or enrichment in the hook. It runs on EVERY prompt and must be <100ms. Keep it to a simple JSON append.

### Recommendation
PROCEED -- direction is sound. Keep the hook minimal (append-only log), keep the sync script authoritative (reads only history.jsonl), keep the search CLI simple (reads SQLite).

## Detailed Analysis

### Architecture Overview

```
Layer 1: CAPTURE (real-time)
  UserPromptSubmit hook --> ~/.claude/prompt-library/capture.jsonl
  (append-only, <50ms, fire-and-forget)

Layer 2: SYNC (on-demand or cron)
  ~/.claude/history.jsonl --> sync script --> markdown vault + SQLite DB
  (idempotent, full rebuild every time, ~1-2 seconds for current data)

Layer 3: SEARCH (terminal CLI)
  User query --> SQLite FTS5 --> ranked results with file links
```

### Data Flow

```
history.jsonl (2999+ prompts)
  |
  v
sync.py --+---> ~/.claude/prompt-library/vault/
           |      2026/
           |        01/
           |          2026-01-19__session-uuid__first-prompt-words.md
           |          ...
           |        02/
           |          ...
           |      _index.md (vault-level index)
           |
           +---> ~/.claude/prompt-library/prompts.db
                   prompts (id, session_id, prompt_text, timestamp, project)
                   conversations (session_id, name, project, start_ts, end_ts, prompt_count)
                   prompts_fts (FTS5 virtual table)
```

### File Organization: Date-based (YYYY/MM/)

Rationale:
- Flat: 894+ files in one directory = unusable in Obsidian sidebar
- Project-based: prompts span projects, some have no clear project
- Date-based: natural chronological browsing, maps to Obsidian calendar plugin, predictable paths

### Conversation Naming Strategy

Format: `YYYY-MM-DD__<session-id-prefix>__<slug>.md`

Where `<slug>` is derived from the first non-command prompt:
1. Take first 60 chars of first real prompt (skip `/` commands)
2. Lowercase, replace non-alphanumeric with hyphens, collapse multiple hyphens
3. If empty or too short, use `session-<first-8-chars-of-uuid>`

Example: `2026-01-19__b300fdf4__ciao.md`
Example: `2026-01-19__15908c6e__sto-preparando-un-prompt-da-usare-con-claude-code.md`

### Markdown Format (Obsidian-compatible)

```markdown
---
session_id: b300fdf4-bb53-478e-b0b5-00c1bffe2f96
project: /Users/silvio.pavanetto
started: 2026-01-19T10:14:17
ended: 2026-01-19T10:29:59
prompt_count: 2
tags:
  - claude-code
  - prompt-library
---

# Ciao

**Project:** `/Users/silvio.pavanetto`
**Duration:** 2026-01-19 10:14 - 10:29 (15 min)
**Prompts:** 2

---

## Prompt 1 - 10:14:17

ciao!

## Prompt 2 - 10:15:02

come stai?
```

### SQLite Schema

```sql
CREATE TABLE conversations (
    session_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project TEXT,
    start_ts INTEGER NOT NULL,  -- epoch ms
    end_ts INTEGER NOT NULL,    -- epoch ms
    prompt_count INTEGER NOT NULL,
    md_path TEXT                 -- relative path to markdown file
);

CREATE TABLE prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES conversations(session_id),
    prompt_text TEXT NOT NULL,
    timestamp INTEGER NOT NULL,  -- epoch ms
    project TEXT,
    seq INTEGER NOT NULL         -- order within conversation
);

CREATE VIRTUAL TABLE prompts_fts USING fts5(
    prompt_text,
    content=prompts,
    content_rowid=id
);

-- Rebuild FTS index
INSERT INTO prompts_fts(prompts_fts) VALUES('rebuild');
```

### Search CLI

```bash
# Full-text search
prompt-search "database migration"

# Recent prompts
prompt-search --recent 10

# Search within project
prompt-search --project auth "API endpoint"

# List conversations for a date
prompt-search --date 2026-03-25
```

Implementation: single Python script, reads SQLite, outputs formatted results with file paths.

## Implementation Steps

### Step 1: Create directory structure
- Create `~/.claude/prompt-library/`
- Create `~/.claude/prompt-library/vault/`
- Create `~/.claude/prompt-library/hooks/`

### Step 2: Write the UserPromptSubmit hook script
- File: `~/.claude/prompt-library/hooks/capture-prompt.py`
- Reads JSON from stdin, appends to `~/.claude/prompt-library/capture.jsonl`
- Must be <50ms, no stdout (silent), exit 0
- Handle errors gracefully (never block Claude Code)

### Step 3: Register hook in ~/.claude/hooks.json
- Add `UserPromptSubmit` entry alongside existing `PreToolUse` GitNexus hook
- Command: `python3 ~/.claude/prompt-library/hooks/capture-prompt.py`
- Timeout: 5000ms (generous, script should take <50ms)

### Step 4: Write the sync script
- File: `~/.claude/prompt-library/sync.py`
- Reads `~/.claude/history.jsonl` (authoritative source)
- Groups prompts by sessionId
- Filters out slash commands (`/help`, `/compact`, etc.)
- For each conversation:
  - Generates descriptive name from first prompt
  - Creates markdown file in `vault/YYYY/MM/` directory
  - With YAML frontmatter (Obsidian-compatible)
- Creates/rebuilds SQLite DB with FTS5
- Idempotent: safe to run repeatedly (full rebuild)
- Prints summary: N conversations, N prompts, N new since last sync

### Step 5: Write the search CLI
- File: `~/.claude/prompt-library/search.py`
- Symlink or alias: `prompt-search`
- Subcommands: search (default), recent, list, stats
- Output: colored terminal output with prompt snippets and file paths
- Uses SQLite FTS5 `MATCH` with `bm25()` ranking

### Step 6: Add shell alias
- Add to `~/.zshrc`: `alias prompt-search='python3 ~/.claude/prompt-library/search.py'`
- Add to `~/.zshrc`: `alias prompt-sync='python3 ~/.claude/prompt-library/sync.py'`

### Step 7: Initial sync run
- Run `prompt-sync` to generate vault from existing 2999 prompts / 894 sessions
- Verify markdown output in Obsidian
- Verify FTS search works

### Files to create (all new, no existing files modified except hooks.json):
1. `~/.claude/prompt-library/hooks/capture-prompt.py` -- hook script
2. `~/.claude/prompt-library/sync.py` -- sync script
3. `~/.claude/prompt-library/search.py` -- search CLI
4. `~/.claude/hooks.json` -- EDIT: add UserPromptSubmit entry

### Files generated by sync (not manually created):
- `~/.claude/prompt-library/vault/YYYY/MM/*.md` -- conversation markdown files
- `~/.claude/prompt-library/vault/_index.md` -- vault index
- `~/.claude/prompt-library/prompts.db` -- SQLite database
