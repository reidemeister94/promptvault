---
paths:
  - "promptvault/**"
---

# Data model & storage

## Sources (both merged into one vault + DB)

- **Claude Code** — `~/.claude/history.jsonl` (flat prompt log, ms timestamps). `source='claude-code'`.
- **Codex** — `~/.codex/sessions/**/rollout-*.jsonl` (auto-detected if present). User prompts = `event_msg`/`user_message` records; project = `session_meta.payload.cwd`; ISO timestamps → ms. `source='codex'`. Flat `~/.codex/history.jsonl` is NOT used (no project, ts in seconds).

Session IDs are disjoint UUIDs across tools → `main()` merges the two session maps by `session_id`; `source` is the discriminator (a column + a markdown tag + an fzf badge/filter), never a namespace prefix.

## prompts.db (rebuilt on every sync)

```
conversations(session_id PK, name, display_name, project, start_ts, end_ts, prompt_count, md_path, source)
prompts(id PK, session_id FK, prompt_text, timestamp, project, seq)
prompts_fts USING fts5(prompt_text, content=prompts)  -- BM25 ranking
```

`source` defaults to `'claude-code'`; `_fts_search` and `_build_prompt_lines` SELECT `c.source` (7-tuple: prompt_text, ts, project, conv_name, md_path, source, rank) — keep all unpackers in lockstep.

## tags.db (separate DB — survives sync rebuilds)

```
tags(session_id TEXT, tag TEXT, created_ts INTEGER, PRIMARY KEY(session_id, tag))
```

## FTS5 gotcha

Queries are sanitized via `_fts_tokenize()` before MATCH: hyphens → spaces (`best-pr` → `best pr*`); FTS5 operators `/ " + * ( ) ^ ~ :` stripped. Without this, FTS5 throws syntax errors or silently returns wrong results.

## Env vars

| Variable | Default |
|----------|---------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` |
| `PROMPTVAULT_VAULT` | `~/.claude/prompt-library/vault` |
| `PROMPTVAULT_PROJECTS` | `~/.claude/projects` |
| `PROMPTVAULT_PASTE_CACHE` | `~/.claude/paste-cache` |
| `PROMPTVAULT_CODEX_SESSIONS` | `~/.codex/sessions` |

Tests: an autouse `_isolate_codex` fixture pins `PROMPTVAULT_CODEX_SESSIONS` to a nonexistent path so no test reads the real `~/.codex`.
