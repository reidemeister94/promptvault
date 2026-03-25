# Research: Claude Code Prompt Library

Related plan: 0001__2026-03-25__implementation_plan__claude-prompt-library.md

## Selected Approach

**Recommended:** Sync-primary with lightweight hook capture
**Why:** The `history.jsonl` file already contains every user prompt with timestamps, session IDs, and project paths. A sync script that reads this file is the most reliable data source. A UserPromptSubmit hook adds real-time capture to a simple append-only log, providing a clean secondary source and enabling future real-time features.
**Key implementation guidance:**
- `~/.claude/history.jsonl` is the canonical, authoritative data source -- it already has 2999 prompts across 894 sessions
- Each line is JSON: `{"display": "prompt text", "pastedContents": {}, "timestamp": 1768818857253, "project": "/path/to/project", "sessionId": "uuid"}`
- Timestamps are Unix epoch milliseconds
- Session JSONL files in `~/.claude/projects/<encoded-path>/<session-id>.jsonl` contain full conversation data but are complex (tool calls, metadata, sidechains)
- Python 3's sqlite3 module supports FTS5 (verified: SQLite 3.45.3 on this machine)
- UserPromptSubmit hook receives `{"session_id", "transcript_path", "cwd", "hook_event_name", "prompt"}` on stdin
**Anti-patterns to avoid:** Do NOT parse session JSONL files for prompt extraction -- history.jsonl is simpler, complete, and authoritative. Do NOT use external dependencies. Do NOT try to generate conversation names via LLM calls from hooks (latency, cost, complexity).

## Web Research

### Claude Code Hooks - UserPromptSubmit Schema
**Query:** "Claude Code hooks UserPromptSubmit 2026 documentation stdin JSON schema"
**Queried:** 2026-03-25
**Key findings:**
- UserPromptSubmit stdin JSON: `{"session_id": "str", "transcript_path": "str", "cwd": "str", "permission_mode": "str", "hook_event_name": "UserPromptSubmit", "prompt": "str"}` -- Source: [Hooks reference](https://code.claude.com/docs/en/hooks)
- UserPromptSubmit does NOT support matchers -- triggers on every prompt -- Source: [Hooks reference](https://code.claude.com/docs/en/hooks)
- Exit code 0 with plain stdout text injects into Claude's context -- Source: [Hooks reference](https://code.claude.com/docs/en/hooks)
- 8 hook events total: PreToolUse, PostToolUse, Notification, UserPromptSubmit, Stop, SubagentStop, PreCompact, SessionStart -- Source: [Hooks schemas gist](https://gist.github.com/FrancisBourre/50dca37124ecc43eaf08328cdcccdb34)
- Hook config goes in `~/.claude/hooks.json` or `~/.claude/settings.json` under `"hooks"` key -- Source: [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
**Relevance:** Defines the exact data available at hook time and configuration format.

### Claude Code Hooks Configuration Format
**Query:** "Claude Code hooks configuration UserPromptSubmit PreToolUse 2025 2026"
**Queried:** 2026-03-25
**Key findings:**
- Hook config structure: `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "script", "timeout": N}]}]}}` -- Source: [Automate workflows](https://code.claude.com/docs/en/hooks-guide)
- `hooks.json` in `~/.claude/hooks.json` is already used by user (GitNexus hook on PreToolUse) -- verified in codebase
- Timeout defaults to 600 seconds max -- Source: [Hooks reference](https://code.claude.com/docs/en/hooks)
**Relevance:** Confirms how to add UserPromptSubmit hook without conflicting with existing PreToolUse hooks.

## Codebase Analysis

### ~/.claude/history.jsonl -- Primary Data Source
**Files examined:** `~/.claude/history.jsonl`
**Structure:** One JSON object per line, append-only
**Schema per line:**
```json
{
  "display": "the prompt text shown to user",
  "pastedContents": {"1": {"id": 1, "type": "text", "contentHash": "hex"}},
  "timestamp": 1768818857253,
  "project": "/Users/silvio.pavanetto/path/to/project",
  "sessionId": "uuid-v4"
}
```
**Statistics:**
- 2999 total prompt entries
- 894 unique sessions
- Timestamps are Unix epoch milliseconds
- `pastedContents` contains metadata about pasted content but NOT the content itself (just hash)
- Commands like `/help`, `/compact`, `/mcp list` are also recorded
- `project` field maps to the working directory / project root

**Implications for implementation:**
- This is the single source of truth -- no need to parse session JSONL files
- Conversation grouping = group by `sessionId`
- Conversation start time = min timestamp per session
- Conversation end time = max timestamp per session
- Conversation project = `project` field (consistent within session)
- Need to filter out slash commands (`/help`, `/compact`, etc.) from the prompt library
- `pastedContents` hashes could be used for dedup but content is NOT available here

### ~/.claude/sessions/ -- Session Metadata
**Files examined:** `~/.claude/sessions/19346.json`, `~/.claude/sessions/64704.json`
**Schema:**
```json
{
  "pid": 19346,
  "sessionId": "uuid",
  "cwd": "/path/to/project",
  "startedAt": 1774462540030,
  "kind": "interactive"
}
```
**Implications:** Maps PID to session -- useful for checking if session is still active, but NOT needed for the prompt library.

### ~/.claude/projects/<encoded-path>/sessions-index.json -- Session Metadata with Titles
**Files examined:** Multiple `sessions-index.json` files across projects
**Discovery date:** 2026-03-26
**Structure:** JSON with `version` and `entries` array. Each entry has `sessionId`, `summary` (auto-generated title), `firstPrompt`, `messageCount`, `created`, `modified`, `gitBranch`, `projectPath`.
**Key field:** `summary` contains Claude Code's auto-generated conversation title (3-6 words, well-formatted). This is the same title shown by `claude --resume`.
**Coverage:** Only 132 of 902 sessions have summaries. Only 9 of 23 projects have the file. Appears to have been introduced in a specific Claude Code version and is not reliably maintained across all sessions.
**Implications:** Excellent source for conversation titles when available, but MUST have a fallback. We use `summary` → first-prompt-based title → `(no text prompts)` as the cascade.

### ~/.claude/projects/<encoded-path>/<session-id>.jsonl -- Full Conversation
**Files examined:** Multiple session JSONL files
**Structure:** Rich JSONL with types: `file-history-snapshot`, `progress`, `user`, `assistant`, `tool_use`, etc.
**Implications:** Contains full conversation including Claude's responses and tool usage. Overly complex for prompt-only extraction. history.jsonl is sufficient.

### ~/.claude/hooks.json -- Existing Hook Configuration
**Files examined:** `~/.claude/hooks.json`
**Current state:** Has one PreToolUse hook for GitNexus
**Implications:** Adding UserPromptSubmit hooks will NOT conflict -- different event type. Can add to same file.

## Reusable Patterns

### history.jsonl Parsing Pattern
**Where found:** Verified from actual `~/.claude/history.jsonl`
**What it does:** Extracts all user prompts grouped by session
**Code:**
```python
import json
from collections import defaultdict

def parse_history(history_path):
    sessions = defaultdict(list)
    with open(history_path) as f:
        for line in f:
            entry = json.loads(line.strip())
            # Skip slash commands
            if entry["display"].startswith("/"):
                continue
            sessions[entry["sessionId"]].append(entry)
    return sessions
```

### FTS5 Table Creation Pattern
**Where found:** Python stdlib docs + verified on machine
**What it does:** Creates searchable full-text index
**Code:**
```python
import sqlite3

conn = sqlite3.connect("prompts.db")
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
        prompt_text,
        session_id UNINDEXED,
        project UNINDEXED,
        timestamp UNINDEXED,
        content=prompts,
        content_rowid=id
    )
""")
```

### UserPromptSubmit Hook Script Pattern
**Where found:** Claude Code hooks documentation
**What it does:** Captures prompt in real-time via hook
**Code:**
```python
#!/usr/bin/env python3
import sys, json, os, time

data = json.load(sys.stdin)
log_path = os.path.expanduser("~/.claude/prompt-library/capture.jsonl")
os.makedirs(os.path.dirname(log_path), exist_ok=True)

entry = {
    "prompt": data["prompt"],
    "session_id": data["session_id"],
    "cwd": data["cwd"],
    "timestamp": int(time.time() * 1000),
    "hook_event_name": data["hook_event_name"]
}

with open(log_path, "a") as f:
    f.write(json.dumps(entry) + "\n")
# Exit 0 silently -- no stdout means no context injection
```

## Sources

| # | Source | URL | Trust Tier |
|---|--------|-----|------------|
| 1 | Claude Code Hooks Reference (Official) | https://code.claude.com/docs/en/hooks | 1 |
| 2 | Claude Code Hooks Guide (Official) | https://code.claude.com/docs/en/hooks-guide | 1 |
| 3 | Hook Schemas Gist (FrancisBourre) | https://gist.github.com/FrancisBourre/50dca37124ecc43eaf08328cdcccdb34 | 4 |
| 4 | Actual ~/.claude/history.jsonl | Local file, verified | 1 |
| 5 | Actual ~/.claude/hooks.json | Local file, verified | 1 |
| 6 | Python sqlite3 FTS5 | Local verification (SQLite 3.45.3) | 1 |

## Rejected Alternatives (reference only)

| Approach | Pros | Cons | Why Rejected |
|----------|------|------|--------------|
| Parse session JSONL files for prompts | Rich data (responses, tools) | Complex nested structure, multiple types, sidechains, metadata noise | history.jsonl already has all prompts cleanly -- session JSONL is overengineered for this use case |
| Hook-only approach (no sync) | Real-time, simple | Misses all historical data (2999 prompts), hook failures = lost data | Cannot retroactively capture existing history |
| LLM-generated conversation names | Descriptive, human-quality | Requires API calls, adds latency to hook, cost, external dependency | First-prompt-based naming is good enough; can upgrade later |
| SQLite as sole store (no markdown) | Single source of truth | Not browsable in Obsidian, not grep-able, opaque | Markdown files are the primary UX; SQLite is the search index |
| Markdown as sole store (no SQLite) | Simple, portable | FTS requires reading all files; slow at scale with 894+ sessions | SQLite FTS5 provides instant search; markdown for browsing |
