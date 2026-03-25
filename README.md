<p align="center">
  <img src="docs/images/social-preview.svg" alt="promptvault" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/reidemeister94/promptvault/releases"><img src="https://img.shields.io/github/v/release/reidemeister94/promptvault?style=flat-square&color=blue" alt="Release"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/reidemeister94/promptvault?style=flat-square" alt="License"/></a>
  <a href="https://github.com/reidemeister94/promptvault/stargazers"><img src="https://img.shields.io/github/stars/reidemeister94/promptvault?style=flat-square&color=yellow" alt="Stars"/></a>
  <a href="https://github.com/reidemeister94/promptvault/issues"><img src="https://img.shields.io/github/issues/reidemeister94/promptvault?style=flat-square" alt="Issues"/></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/dependencies-zero-22c55e?style=flat-square" alt="Zero Dependencies"/>
</p>

<p align="center">
  <b>Your Claude Code conversations, searchable forever.</b>
</p>

<p align="center">
  <a href="#the-problem">Problem</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#commands">Commands</a> &middot;
  <a href="#real-time-capture">Real-Time Capture</a> &middot;
  <a href="#architecture">Architecture</a>
</p>

---

## The Problem

Claude Code stores conversation history in `~/.claude/history.jsonl` — a raw, append-only JSONL file.

- **Not searchable.** Finding a prompt means grepping through thousands of JSON lines.
- **Not browsable.** No conversation grouping, timestamps, or context.
- **Not persistent.** Claude Code compacts and deletes old session files without warning.
- **Not shareable.** Raw JSONL doesn't open in Obsidian or publish on GitHub.

**promptvault** turns that history into a searchable markdown library + SQLite database. Browse conversations in Obsidian, or search them from the terminal. Zero dependencies — pure Python stdlib.

<p align="center">
  <img src="docs/images/terminal-demo.svg" alt="promptvault in action" width="100%"/>
</p>

---

## How It Works

<p align="center">
  <img src="docs/images/how-it-works.svg" alt="How promptvault works" width="100%"/>
</p>

`promptvault-sync` reads your Claude Code history, groups prompts by conversation, and generates:

1. **Markdown vault** — One `.md` file per conversation, organized by `YYYY/MM/`, with YAML frontmatter. Drop the folder into Obsidian and browse your prompt history.

2. **SQLite database** — FTS5 full-text search with BM25 ranking. Search 3000+ prompts in milliseconds.

The sync is **idempotent** — it always rebuilds from `history.jsonl`, so it's impossible to reach a bad state.

---

## Quick Start

```bash
git clone https://github.com/reidemeister94/promptvault.git
cd promptvault
pip install -e .
```

```bash
promptvault-sync
```

```
Reading history from /Users/you/.claude/history.jsonl...
Found 895 conversations, 3009 prompts
Generating markdown vault...
Building SQLite database...

Done! Vault: ~/.claude/prompt-library/vault
Database: ~/.claude/prompt-library/prompts.db
```

```bash
promptvault search "database migration"
```

---

## Commands

### `promptvault search "query"`

Full-text search using SQLite FTS5 with BM25 ranking.

```bash
promptvault search "shipping scheduler"
promptvault search "pytest fixtures" -n 5
```

### `promptvault recent [N]`

Show most recent prompts. Defaults to 10.

```bash
promptvault recent       # last 10
promptvault recent 20    # last 20
```

### `promptvault list`

List conversations. Filter by date or project.

```bash
promptvault list                          # all conversations
promptvault list --date 2026-03-25        # today's conversations
promptvault list --project shipping       # filter by project name
promptvault list --date 2026-03-25 -n 5   # today's top 5
```

### `promptvault stats`

Vault overview — conversation count, prompt count, top projects, date range.

```bash
promptvault stats
```

### `promptvault-sync`

Rebuild the vault and database from `~/.claude/history.jsonl`. Idempotent.

```bash
promptvault-sync
```

---

## Markdown Vault

Each conversation becomes an Obsidian-compatible markdown file:

```markdown
---
session_id: c792e74f-c1bf-4bd1-af69-795b50f355b4
project: /Users/you/my-project
started: 2026-03-25T18:51:48
ended: 2026-03-25T19:20:05
prompt_count: 10
tags:
  - claude-code
  - promptvault
---

# Refactor the shipping scheduler API endpoint

**Project:** `my-project`
**Duration:** 2026-03-25 18:51 - 19:20 (28 min)
**Prompts:** 10

---

## Prompt 1 — 18:51:48

refactor the shipping scheduler API to use the new service layer...

## Prompt 2 — 18:55:12

can you add proper error handling for the database connection?
```

### Vault Structure

```
~/.claude/prompt-library/vault/
├── _index.md                    # Global index with links to all conversations
├── 2026/
│   ├── 01/
│   │   ├── 2026-01-19__b300fdf4__first-prompt-slug.md
│   │   └── ...
│   ├── 02/
│   │   └── ...
│   └── 03/
│       ├── 2026-03-25__c792e74f__refactor-shipping-scheduler.md
│       └── ...
```

Open `~/.claude/prompt-library/vault/` as an Obsidian vault. The Calendar plugin works well for chronological browsing.

---

## Real-Time Capture

A Claude Code hook captures every prompt the moment you send it — no sync needed.

### Setup

The hook registers automatically on install, adding a `UserPromptSubmit` entry to `~/.claude/hooks.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/promptvault/promptvault/hook.py",
        "timeout": 5000
      }]
    }]
  }
}
```

The hook is:
- **Fast** — <50ms, pure JSON append
- **Silent** — no stdout, doesn't inject into Claude's context
- **Safe** — errors are swallowed, never blocks Claude Code

Captured prompts go to `~/.claude/prompt-library/capture.jsonl` — a real-time log queryable between syncs.

---

## Architecture

```
promptvault/
├── promptvault/
│   ├── sync.py       # Reads history.jsonl → generates vault/ + prompts.db
│   ├── search.py     # CLI search over SQLite FTS5
│   └── hook.py       # UserPromptSubmit hook (real-time capture)
├── tests/
│   ├── test_sync.py
│   ├── test_search.py
│   └── test_hook.py
├── pyproject.toml
└── README.md
```

### Design Decisions

| Decision | Why |
|----------|-----|
| **`history.jsonl` as source of truth** | Authoritative, always complete, maintained by Claude Code. Session JSONL files are too complex for prompt-only extraction. |
| **Full rebuild on every sync** | Simpler than incremental — no state bugs, no dedup logic. ~1 second for 3000 prompts. |
| **Date-based directory structure** | Flat directories are unusable at 900+ files. Project-based grouping breaks when prompts span projects. Date-based maps to Obsidian's Calendar plugin. |
| **SQLite FTS5 for search** | Built into Python stdlib. BM25 ranking included. No external engine needed. |
| **Zero external dependencies** | Python stdlib has everything: `json`, `sqlite3`, `pathlib`, `argparse`. |
| **Hook as convenience, not requirement** | The sync script is the authoritative data path. If the hook fails, nothing is lost. |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` | Claude Code history file |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` | Output directory for vault + DB |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` | SQLite database |
| `PROMPTVAULT_CAPTURE_LOG` | `~/.claude/prompt-library/capture.jsonl` | Real-time capture log |

---

## Development

```bash
git clone https://github.com/reidemeister94/promptvault.git
cd promptvault
pip install -e .
make setup-dev-env   # Install pre-commit hooks

make test            # Run tests
make lint            # Lint with ruff
make format          # Format with ruff
```

32 tests covering sync, search, and hook functionality. All tests use synthetic data — no dependency on real `history.jsonl`.

---

## Roadmap

- [ ] **Incremental sync** — Only process new prompts since last sync
- [ ] **Claude response capture** — Include Claude's responses (from session JSONL files)
- [ ] **Obsidian plugin** — Native sidebar, auto-sync, graph view integration
- [ ] **TUI browser** — Interactive terminal UI with `textual`
- [ ] **Export formats** — HTML, PDF, JSON export
- [ ] **Multi-tool support** — Parse history from Cursor, Copilot, Windsurf, etc.

---

## Contributing

Contributions welcome. Open an issue to discuss before submitting a PR.

**Ideas:**
- New search features (fuzzy search, date ranges, regex)
- Better conversation naming heuristics
- Support for additional AI coding tools
- Performance optimizations for very large histories

---

## License

MIT

---

<p align="center">
  <a href="https://github.com/reidemeister94/promptvault/stargazers"><img src="https://img.shields.io/github/stars/reidemeister94/promptvault?style=social" alt="Star on GitHub"/></a>
</p>

<p align="center">
  <a href="https://github.com/reidemeister94/promptvault/issues">Report an issue</a> &middot; <a href="#contributing">Contribute</a>
</p>
