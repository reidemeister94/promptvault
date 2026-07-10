---
paths:
  - "promptvault/search.py"
---

# fzf TUI (search.py)

After ANY change to keybindings, footer, FTS queries, palette, or display format → run the visual E2E assertions in `.agents/rules/testing.md`. All must exit 0.

## Keybindings

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

## UI design (Catppuccin Mocha)

| Layer | fzf min | What |
|-------|---------|------|
| 24-bit color palette | 0.62 | `bg:-1`, `hl:#cba6f7`, `hl+:#fab387:bold`, `prompt:#b4befe`, `marker:#a6e3a1`, `alt-bg:#313244` |
| Section borders | 0.58 | `--input-border=rounded` + `--input-label= N conversations ` + `--preview-border=line` + `--preview-label= Preview ` |
| ANSI result lines | any | Star=green(`GREEN_24`), date+count=`DIM`, project=`LAVENDER`(24-bit), title=unstyled |
| Footer `·` groups | 0.53 | `^t mode · ^p proj · ^d date · ^b ★fav · ^g show★` (max 66 chars for 80-col) |
| Fallback (<0.58) | — | Single `--border=rounded`, 256-color, plain footer |

**ANSI constants** (search.py): `BOLD`, `DIM`, `CYAN`, `GREEN`, `YELLOW`, `RESET`, `LAVENDER`(24-bit), `GREEN_24`(24-bit)

## Shell widget

`eval "$(pv shell-init zsh)"` (bash: `bash`). Keybinding Alt-P, configurable via `PROMPTVAULT_WIDGET_KEY`.
