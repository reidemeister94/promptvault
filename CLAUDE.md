## Project Quick Reference

| Item | Value |
|------|-------|
| **Language** | Python 3.10+, zero runtime deps |
| **Entry points** | `promptvault` → `search:main`, `promptvault-sync` → `sync:main` |
| **Source** | `promptvault/sync.py`, `promptvault/search.py`, `promptvault/hook.py` |
| **Tests** | `tests/` — 187 tests, pytest, synthetic data only |
| **Lint/Format** | ruff (line-length=100) |
| **Python env** | `/opt/anaconda3/envs/promptvault` |

### DB Schema (prompts.db)

```
conversations(session_id PK, name, display_name, project, start_ts, end_ts, prompt_count, md_path)
prompts(id PK, session_id FK, prompt_text, timestamp, project, seq)
prompts_fts USING fts5(prompt_text, content=prompts)  -- BM25 ranking
```

### Key Env Vars

| Variable | Default |
|----------|---------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` |
| `PROMPTVAULT_VAULT` | `~/.claude/prompt-library/vault` |
| `PROMPTVAULT_PROJECTS` | `~/.claude/projects` |

### Testing

```bash
/opt/anaconda3/envs/promptvault/bin/python -m pytest tests/ -v
```

- `conftest.py`: `tmp_history`, `tmp_output` fixtures (synthetic history.jsonl)
- `test_e2e.py`: `e2e_env` fixture (11 sessions, full vault+DB, covers all edge cases)
- All tests use synthetic data — never touch real `~/.claude/`

---

### Core Pillars

1. **Maximize simplicity, minimize complexity.** Weigh complexity cost against improvement magnitude.
2. **All signal, zero noise.** Everything must earn its place — if it doesn't add value, remove it.
3. **Document every discovery.** Write insights immediately (CLAUDE.md, chronicles, plans).
4. **Comments explain why, not what.** Comment non-obvious business logic, flows, and workarounds only.


## MANDATORY: Read Before Any Task

USE ALWAYS THE PLUGIN "development-skills" FOR EVERY TASK ON THIS PROJECT (BRAINSTORMING, DEVELOPMENT, BUG FIXING, NEW FEATURE, ...)

**Treat documentation as a first-class output of every task.**

### What to do during every task

1. **Document as you go.** Write patterns, gotchas, data shapes, naming conventions, workarounds immediately. Default: CLAUDE.md for project-wide, MEMORY.md for cross-session.
1a. **Code is documentation.** Comments explain *why*, not *what*. See Pillar #4.
2. **Remove ambiguity for your future self.** If you investigated something, write the answer where you'll find it next time.
3. **Use the right document:**

   | Document | Purpose | When to update |
   |----------|---------|----------------|
   | **CLAUDE.md** | Project-wide knowledge, conventions, rules. Loaded every conversation. | Architectural patterns, API shapes, DB schemas, testing conventions, gotchas |
   | **MEMORY.md** | Cross-session memory. Confirmed facts, user preferences. | Confirmed patterns, user corrections, expressed preferences |
   | **docs/plans/** | Implementation plans with checklists. Convention: `NNNN__YYYY-MM-DD__implementation_plan__slug.md` | Start (create), during (update checklist + log), completion (mark status) |
   | **docs/chronicles/** | Discoveries, debugging sessions, design decisions. | Surprising or non-obvious findings |
   | **Other docs/** | Domain docs | Business domain, data model, external systems |

4. **Make CLAUDE.md a cheat sheet, not a novel.** Tables, code snippets, direct statements. See Pillar #2.
5. **Keep documents aligned.** No duplication — each document has its own purpose.

### What NOT to do

- Don't defer documentation — it gets lost when context compresses
- Don't write unverified facts
- Don't bloat CLAUDE.md with session-specific details (use plans/chronicles)
- Don't duplicate — link instead
