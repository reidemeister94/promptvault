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


## Project Quick Reference

| Item | Value |
|------|-------|
| **Language** | Python 3.10+, zero runtime deps |
| **Entry points** | `promptvault` → `search:main`, `promptvault-sync` → `sync:main` |
| **Source** | `promptvault/sync.py`, `promptvault/search.py`, `promptvault/hook.py` |
| **Tests** | `tests/` — 340 tests, pytest, synthetic data only |
| **Lint/Format** | ruff (line-length=100) |
| **Python env** | `/opt/anaconda3/envs/promptvault` |

### DB Schema (prompts.db)

```
conversations(session_id PK, name, display_name, project, start_ts, end_ts, prompt_count, md_path)
prompts(id PK, session_id FK, prompt_text, timestamp, project, seq)
prompts_fts USING fts5(prompt_text, content=prompts)  -- BM25 ranking
```

**FTS5 gotcha:** Queries are sanitized via `_fts_tokenize()` before MATCH. Hyphens become spaces (`best-pr` → `best pr*`), slashes and other FTS5 operators (`/ " + * ( ) ^ ~ :`) are stripped. Without this, FTS5 throws syntax errors or returns wrong results silently.

### Tags Schema (tags.db — separate, survives sync rebuilds)

```
tags(session_id TEXT, tag TEXT, created_ts INTEGER, PRIMARY KEY(session_id, tag))
```

### fzf Keybindings

| Key | Action | fzf min |
|-----|--------|---------|
| enter | Open in editor (returns to fzf) | any |
| ctrl-o | Open in editor (exits fzf) | 0.38 |
| ctrl-y | Copy to clipboard | any |
| ctrl-e | Export to file | any |
| ctrl-x | Exclude from results | 0.60 |
| ctrl-/ | Toggle preview | any |
| ctrl-t | Toggle conv/prompt mode | 0.45 |
| ctrl-p | Cycle project filter | 0.45 |
| ctrl-d | Cycle date filter | 0.45 |
| ctrl-b | Toggle bookmark | 0.45 |
| ctrl-g | Toggle bookmark filter | 0.45 |
| alt-r | Toggle raw mode | 0.66 |
| tab | Multi-select | any |

### fzf UI Design (Catppuccin Mocha palette)

| Layer | fzf min | What |
|-------|---------|------|
| 24-bit color palette | 0.62 | `bg:-1`, `hl:#cba6f7`, `hl+:#fab387:bold`, `prompt:#b4befe`, `marker:#a6e3a1`, `alt-bg:#313244` |
| Section borders | 0.58 | `--input-border=rounded` + `--input-label= N conversations ` + `--preview-border=line` + `--preview-label= Preview ` |
| ANSI result lines | any | Star=green(`GREEN_24`), date+count=`DIM`, project=`LAVENDER`(24-bit), title=unstyled |
| Footer `·` groups | 0.53 | `^t mode · ^p proj · ^d date · ^b ★fav · ^g show★` (max 66 chars for 80-col) |
| Fallback (<0.58) | — | Single `--border=rounded`, 256-color, plain footer |

**ANSI constants** (search.py): `BOLD`, `DIM`, `CYAN`, `GREEN`, `YELLOW`, `RESET`, `LAVENDER`(24-bit), `GREEN_24`(24-bit)

### Shell Widget

```bash
# zsh: eval "$(pv shell-init zsh)"
# bash: eval "$(pv shell-init bash)"
# Keybinding: Alt-P (configurable via PROMPTVAULT_WIDGET_KEY)
```

### Key Env Vars

| Variable | Default |
|----------|---------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` |
| `PROMPTVAULT_VAULT` | `~/.claude/prompt-library/vault` |
| `PROMPTVAULT_PROJECTS` | `~/.claude/projects` |
| `PROMPTVAULT_PASTE_CACHE` | `~/.claude/paste-cache` |

### Testing

```bash
/opt/anaconda3/envs/promptvault/bin/python -m pytest tests/ -v
```

- `conftest.py`: `tmp_history`, `tmp_output` fixtures (synthetic history.jsonl)
- `test_e2e.py`: `e2e_env` fixture (11 sessions, full vault+DB, covers all edge cases)
- All tests use synthetic data — never touch real `~/.claude/`

### Visual E2E Testing (MANDATORY after UI changes)

Uses `pexpect` + `pyte` to drive fzf in a real PTY and capture screen state.
Requires: `pip install pexpect pyte`. The DSR (Device Status Report) response
handler in `FzfHarness._read_output()` is critical — fzf sends `ESC[6n` and
hangs without a response.

```bash
PY=/opt/anaconda3/envs/promptvault/bin/python

# Assertion-based tests (exit code 0 = pass, 1 = fail)
$PY tests/visual_test.py --wait 3000 --assert-min 1                          # Default view has results
$PY tests/visual_test.py --query "best-pr" --wait 3000 --assert-min 1        # Hyphen query works
$PY tests/visual_test.py --query "/best-pr" --wait 3000 --assert-min 1       # Slash query works
$PY tests/visual_test.py --keys ctrl-t --wait 3000 --assert-text "prompt>"   # Mode toggle
$PY tests/visual_test.py --cols 80 --wait 3000 --assert-text "^t mode"       # Footer at 80 cols

# JSON output for debugging
$PY tests/visual_test.py --query best --wait 3000 --json

# Human-readable screen dump
$PY tests/visual_test.py --wait 3000
```

**Available assertions:** `--assert-min N`, `--assert-count N`, `--assert-text "str"`, `--assert-no-text "str"`

**Rule:** After ANY change to fzf UI, keybindings, footer, FTS queries, or display format — run the assertion-based visual tests above. All must exit 0.
