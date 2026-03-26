<p align="center">
  <img src="https://raw.githubusercontent.com/reidemeister94/promptvault/main/docs/images/social-preview.svg" alt="promptvault" width="100%"/>
</p>

<p align="center">
  <a href="https://pypi.org/project/promptvault-py/"><img src="https://img.shields.io/pypi/v/promptvault-py?style=flat-square&amp;color=blue&amp;v=2" alt="PyPI"/></a>
  <a href="https://github.com/reidemeister94/promptvault/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/reidemeister94/promptvault/ci.yml?style=flat-square&amp;label=CI" alt="CI"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/reidemeister94/promptvault?style=flat-square" alt="License"/></a>
  <a href="https://github.com/reidemeister94/promptvault/stargazers"><img src="https://img.shields.io/github/stars/reidemeister94/promptvault?style=flat-square&amp;color=yellow" alt="Stars"/></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776ab?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/dependencies-zero-22c55e?style=flat-square" alt="Zero Dependencies"/>
</p>

<p align="center">
  <b>Your Claude Code conversations, searchable forever.</b>
</p>

---

## The Problem

Claude Code stores conversations in `~/.claude/history.jsonl` — not searchable, not browsable, not persistent. Claude compacts and deletes old sessions without warning.

**pv** turns that history into a **searchable markdown library + SQLite database**. Browse in Obsidian, search from the terminal with fzf. Zero dependencies. Pure stdlib.

<p align="center">
  <img src="https://raw.githubusercontent.com/reidemeister94/promptvault/main/docs/images/terminal-demo.svg" alt="pv in action" width="100%"/>
</p>

---

## Quick Start

```bash
# recommended — fast, isolated, always on PATH
uv tool install promptvault-py

# alternative — same idea, traditional tool
pipx install promptvault-py

pv-sync                        # sync your Claude Code history
pv                             # browse conversations
pv search "database migration" # full-text search
```

> Don't have uv? `curl -LsSf https://astral.sh/uv/install.sh | sh` ([docs](https://docs.astral.sh/uv/getting-started/installation/))
>
> Don't have pipx? `brew install pipx` or `pip install --user pipx` ([docs](https://pipx.pypa.io/stable/installation/))

Both tools install `pv` into an **isolated virtualenv** and symlink the executable to `~/.local/bin/`, so it's available globally — no environment activation needed.

> `pv` is the short alias. `promptvault` / `promptvault-sync` also work.

Optional: install [fzf](https://github.com/junegunn/fzf) for the interactive UI (`brew install fzf` / `apt install fzf`). Without it, pv falls back to plain text.

---

## How It Works

<p align="center">
  <img src="https://raw.githubusercontent.com/reidemeister94/promptvault/main/docs/images/how-it-works.svg" alt="How pv works" width="100%"/>
</p>

`pv-sync` reads `history.jsonl`, groups prompts by conversation, and generates:

1. **Markdown vault** — One `.md` per conversation, `YYYY/MM/` structure, YAML frontmatter. Drop into Obsidian.
2. **SQLite database** — FTS5 full-text search with BM25 ranking. Millisecond queries.

The sync is **idempotent** — always rebuilds from source, impossible to reach a bad state. Resolves pasted-text placeholders, deduplicates prompts, filters slash commands, cleans whitespace.

---

## Commands

All commands launch **fzf** by default (split pane: conversation list + live preview). Add `--no-fzf` for plain text.

| Command | Description |
|---------|-------------|
| `pv` | Browse all conversations interactively |
| `pv search "query"` | Full-text search ranked by relevance |
| `pv recent [N]` | Last N conversations (default 20) |
| `pv list [--date DATE] [--project NAME]` | Filter by date or project |
| `pv stats` | Vault overview |
| `pv-sync` | Rebuild vault + database |

**Controls:** `Up/Down` navigate, `Enter` opens in `$EDITOR`, `Ctrl-Y` copies, `Esc` quits

---

## Real-Time Capture

A Claude Code hook captures prompts the moment you send them — no sync needed.

Add to `~/.claude/hooks.json`:

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

Fast (<50ms), silent (no stdout), safe (errors swallowed). Captures to `~/.claude/prompt-library/capture.jsonl`.

> **Windows:** use `python` instead of `python3` and backslash paths.

---

## Markdown Vault

Each conversation becomes an Obsidian-compatible `.md` with YAML frontmatter:

```
~/.claude/prompt-library/vault/
├── _index.md
├── 2026/
│   └── 03/
│       ├── 2026-03-25__c792e74f__refactor-user-auth.md
│       └── ...
```

Open as an Obsidian vault. The Calendar plugin works well for browsing.

---

## Environment Variables

| Variable | Default |
|----------|---------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` |
| `PROMPTVAULT_VAULT` | `~/.claude/prompt-library/vault` |
| `PROMPTVAULT_PROJECTS` | `~/.claude/projects` |
| `PROMPTVAULT_CAPTURE_LOG` | `~/.claude/prompt-library/capture.jsonl` |

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md). 187 tests, all synthetic data.

```bash
git clone https://github.com/reidemeister94/promptvault.git
cd promptvault
uv tool install --editable .   # global "pv" that tracks your local source
make setup-dev-env
make test && make lint
```

---

## License

MIT

---

<p align="center">
  <b>If pv saves your prompts, save us a </b><a href="https://github.com/reidemeister94/promptvault/stargazers"><img src="https://img.shields.io/github/stars/reidemeister94/promptvault?style=social" alt="Star on GitHub"/></a>
</p>

<p align="center">
  <a href="https://github.com/reidemeister94/promptvault/issues">Report an issue</a> &middot; <a href="CONTRIBUTING.md">Contribute</a>
</p>
