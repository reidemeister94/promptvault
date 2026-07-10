---
paths:
  - "promptvault/**"
---

# Data model & storage

## prompts.db (rebuilt on every sync)

```
conversations(session_id PK, name, display_name, project, start_ts, end_ts, prompt_count, md_path)
prompts(id PK, session_id FK, prompt_text, timestamp, project, seq)
prompts_fts USING fts5(prompt_text, content=prompts)  -- BM25 ranking
```

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
