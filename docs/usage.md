# Usage Guide

## Installation

```bash
uv tool install promptvault-py    # recommended
pipx install promptvault-py       # alternative
```

Requires Python 3.10+. Zero runtime dependencies.

Optional: install [fzf](https://github.com/junegunn/fzf) (0.45.0+ recommended) for the full interactive experience.

```bash
brew install fzf    # macOS
apt install fzf     # Debian/Ubuntu
```

## Commands

### `pv` / `pv search [QUERY]`

Browse or search conversations interactively.

```bash
pv                             # browse all conversations
pv search "database migration" # full-text search
pv search "pytest" -n 50       # plain text, 50 results max
pv search --no-fzf "auth"      # force plain text output
```

### `pv recent [N]`

Show the N most recent conversations (default: 20).

```bash
pv recent        # last 20
pv recent 50     # last 50
```

### `pv list`

List conversations with optional filters.

```bash
pv list                              # all conversations
pv list --date 2026-03-25            # filter by date
pv list --project myapp              # filter by project
pv list --date 2026-03-25 --project myapp -n 10
```

### `pv stats`

Show vault statistics: conversation count, prompt count, top projects.

### `pv-sync`

Rebuild the vault and database from `~/.claude/history.jsonl`. Runs automatically when the DB is stale.

---

## Interactive Controls (fzf)

### Navigation

| Key | Action |
|-----|--------|
| `Up/Down` | Navigate conversations |
| `Enter` | Open in `$EDITOR` (returns to fzf after) |
| `Tab/Shift-Tab` | Multi-select conversations |
| `Esc` | Quit |

### Features

| Key | Action |
|-----|--------|
| `Ctrl-T` | Toggle between conversation and prompt views |
| `Ctrl-P` | Cycle through project filters |
| `Ctrl-D` | Cycle date range: all / today / this week / this month |
| `Ctrl-Y` | Copy to clipboard (view-aware) |
| `Ctrl-E` | Export to file (view-aware) |
| `Ctrl-/` | Toggle preview pane |

### Copy and Export

Copy (`Ctrl-Y`) and export (`Ctrl-E`) are **view-aware** — their behavior depends on which view you're in:

| View | Ctrl-Y (copy) | Ctrl-E (export) |
|------|---------------|-----------------|
| **Conversation view** | Copies all prompts from selected conversations | Exports full conversations to `export.md` |
| **Prompt view** | Copies only the selected prompt lines | Exports only the selected prompts to `export.md` |

Use `Tab` to multi-select items before copying or exporting. The export file is written to `~/.claude/prompt-library/export.md`.

### Search

Type to search in real-time. The search uses SQLite FTS5 with BM25 ranking for relevance scoring. Partial words match via prefix search ("pytes" finds "pytest").

### Views

**Conversation view** (default, prompt: `conv> `): Shows conversations grouped by date with prompt count, project, and title.

**Prompt view** (toggle with `Ctrl-T`, prompt: `prompt> `): Shows individual prompts ranked by relevance. Useful for finding a specific prompt across all conversations.

### Filters

**Project cycling** (`Ctrl-P`): Cycles through your projects. The current filter shows in the prompt as `conv [project]> `. Press again to cycle to the next project, or back to "all".

**Date range** (`Ctrl-D`): Cycles through time presets:
- All (no filter)
- Today (conversations from today)
- This week (last 7 days)
- This month (last 30 days)

### Preview

The right pane shows a live preview of the selected conversation with:
- Pinned header: conversation title, project, duration, prompt count
- Prompt content with query highlighting in yellow
- Toggle with `Ctrl-/`

---

## fzf Version Compatibility

promptvault gracefully degrades based on your fzf version:

| Feature | Min fzf version |
|---------|----------------|
| Basic interactive browse | any |
| Highlight line | 0.53+ |
| Footer keybindings | 0.53+ |
| Ghost placeholder text | 0.54+ |
| Mode switching, project/date cycling | 0.45+ |
| tmux popup mode | 0.38+ |

Without fzf installed, all commands fall back to plain text output.

---

## Clipboard Support

Clipboard copy (`Ctrl-Y`) auto-detects the available tool:

| Platform | Tool |
|----------|------|
| macOS | `pbcopy` |
| Linux (Wayland) | `wl-copy` |
| Linux (X11) | `xclip` or `xsel` |

If no clipboard tool is found, the `Ctrl-Y` binding is omitted.

---

## Markdown Vault

`pv-sync` generates an Obsidian-compatible vault:

```
~/.claude/prompt-library/vault/
├── _index.md                    # conversation index
├── 2026/
│   └── 03/
│       ├── 2026-03-25__c792e74f__refactor-auth.md
│       └── ...
```

Each file has YAML frontmatter (`session_id`, `project`, `started`, `ended`, `prompt_count`, `tags`) and prompt sections. Drop the vault directory into Obsidian for browsing.

---

## Real-Time Capture

Capture prompts as you type (no sync needed). Add to `~/.claude/hooks.json`:

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

Fast (<50ms), silent, safe (errors swallowed).

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROMPTVAULT_HISTORY` | `~/.claude/history.jsonl` | Input history file |
| `PROMPTVAULT_OUTPUT` | `~/.claude/prompt-library` | Output directory |
| `PROMPTVAULT_DB` | `~/.claude/prompt-library/prompts.db` | Database path |
| `PROMPTVAULT_VAULT` | `~/.claude/prompt-library/vault` | Vault directory |
| `PROMPTVAULT_PROJECTS` | `~/.claude/projects` | Session summaries |
| `PROMPTVAULT_CAPTURE_LOG` | `~/.claude/prompt-library/capture.jsonl` | Hook capture log |
| `EDITOR` | `less` | Editor for opening files |
