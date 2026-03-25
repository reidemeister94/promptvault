"""Sync engine: reads ~/.claude/history.jsonl and generates markdown vault + SQLite DB."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
DEFAULT_OUTPUT_DIR = Path.home() / ".claude" / "prompt-library"

SLASH_COMMANDS = frozenset(
    {
        "/help",
        "/compact",
        "/clear",
        "/config",
        "/cost",
        "/doctor",
        "/init",
        "/login",
        "/logout",
        "/mcp",
        "/memory",
        "/model",
        "/permissions",
        "/review",
        "/status",
        "/terminal-setup",
        "/vim",
        "/hooks",
        "/listen",
        "/resume",
        "/fast",
    }
)


def resolve_pasted_content(entry: dict) -> str:
    """Replace [Pasted text #N ...] placeholders with actual pasted content."""
    display = entry["display"]
    pasted = entry.get("pastedContents", {})
    if not pasted:
        return display

    for key, paste_info in pasted.items():
        if not isinstance(paste_info, dict):
            continue
        content = paste_info.get("content", "")
        if not content:
            continue
        # Match [Pasted text #N] or [Pasted text #N +M lines]
        pattern = rf"\[Pasted text #{re.escape(key)}[^\]]*\]"
        # re.sub treats backslashes in replacement as escapes — use a lambda to avoid that
        display = re.sub(pattern, lambda _: content.strip(), display)

    return display


def parse_history(history_path: Path) -> dict[str, list[dict]]:
    """Parse history.jsonl into sessions. Each session is a list of prompt entries."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            # Resolve pasted content placeholders into actual text
            entry["display"] = resolve_pasted_content(entry)
            sessions[entry["sessionId"]].append(entry)
    # Sort prompts within each session by timestamp and deduplicate
    for session_id in sessions:
        sessions[session_id].sort(key=lambda e: e["timestamp"])
        # Remove consecutive duplicates (same text within a session, e.g. double-submit)
        deduped: list[dict] = []
        for entry in sessions[session_id]:
            if not deduped or entry["display"].strip() != deduped[-1]["display"].strip():
                deduped.append(entry)
        sessions[session_id] = deduped
    return dict(sessions)


def is_slash_command(prompt: str) -> bool:
    """Check if a prompt is a slash command (should be filtered from vault display)."""
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return False
    # Match known commands or any /word pattern
    cmd = stripped.split()[0].rstrip()
    return cmd in SLASH_COMMANDS or re.match(r"^/[a-z-]+$", cmd) is not None


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL-safe slug."""
    text = text[:max_length].lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")


def make_conversation_name(prompts: list[dict], session_id: str) -> str:
    """Generate a human-readable name from the first non-command prompt."""
    for p in prompts:
        display = p["display"].strip()
        if not is_slash_command(display) and len(display) > 2:
            slug = slugify(display)
            if slug:
                return slug
    return f"session-{session_id[:8]}"


def ts_to_datetime(ts_ms: int) -> datetime:
    """Convert epoch milliseconds to datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def format_duration(start: datetime, end: datetime) -> str:
    """Format duration between two datetimes."""
    delta = end - start
    minutes = int(delta.total_seconds() / 60)
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


def generate_markdown(session_id: str, prompts: list[dict], name: str) -> str:
    """Generate Obsidian-compatible markdown for a conversation."""
    all_prompts = [p for p in prompts if not is_slash_command(p["display"].strip())]
    if not all_prompts:
        all_prompts = prompts  # fallback: include everything if all are commands

    start_dt = ts_to_datetime(prompts[0]["timestamp"])
    end_dt = ts_to_datetime(prompts[-1]["timestamp"])
    project = prompts[0].get("project", "unknown")

    # Title from first real prompt
    title_prompt = all_prompts[0]["display"].strip() if all_prompts else name
    title = title_prompt[:80] if len(title_prompt) > 80 else title_prompt
    # Capitalize first letter
    if title:
        title = title[0].upper() + title[1:]

    lines = [
        "---",
        f"session_id: {session_id}",
        f"project: {project}",
        f"started: {start_dt.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"ended: {end_dt.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"prompt_count: {len(all_prompts)}",
        "tags:",
        "  - claude-code",
        "  - promptvault",
        "---",
        "",
        f"# {title}",
        "",
        f"**Project:** `{project}`",
        f"**Duration:** {start_dt.strftime('%Y-%m-%d %H:%M')} - {end_dt.strftime('%H:%M')}"
        f" ({format_duration(start_dt, end_dt)})",
        f"**Prompts:** {len(all_prompts)}",
        "",
        "---",
    ]

    for i, p in enumerate(all_prompts, 1):
        dt = ts_to_datetime(p["timestamp"])
        lines.append("")
        lines.append(f"## Prompt {i} — {dt.strftime('%H:%M:%S')}")
        lines.append("")
        lines.append(p["display"].strip())

    lines.append("")
    return "\n".join(lines)


def generate_vault(sessions: dict[str, list[dict]], vault_dir: Path) -> dict[str, str]:
    """Generate markdown files for all sessions. Returns {session_id: relative_md_path}."""
    md_paths: dict[str, str] = {}

    for session_id, prompts in sessions.items():
        name = make_conversation_name(prompts, session_id)
        start_dt = ts_to_datetime(prompts[0]["timestamp"])

        # Date-based directory: vault/YYYY/MM/
        year_month_dir = vault_dir / start_dt.strftime("%Y") / start_dt.strftime("%m")
        year_month_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{start_dt.strftime('%Y-%m-%d')}__{session_id[:8]}__{name}.md"
        filepath = year_month_dir / filename

        md_content = generate_markdown(session_id, prompts, name)
        filepath.write_text(md_content, encoding="utf-8")

        # Store relative path from vault root
        md_paths[session_id] = str(filepath.relative_to(vault_dir))

    return md_paths


def generate_index(sessions: dict[str, list[dict]], md_paths: dict[str, str], vault_dir: Path):
    """Generate _index.md at vault root with links to all conversations."""
    # Group by year/month
    by_month: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for session_id, prompts in sessions.items():
        start_dt = ts_to_datetime(prompts[0]["timestamp"])
        key = start_dt.strftime("%Y-%m")
        by_month[key].append((session_id, prompts))

    lines = [
        "# Prompt Vault Index",
        "",
        f"**Total conversations:** {len(sessions)}",
        f"**Total prompts:** {sum(len(p) for p in sessions.values())}",
        f"**Generated:** {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "---",
    ]

    for month_key in sorted(by_month.keys(), reverse=True):
        month_sessions = by_month[month_key]
        month_sessions.sort(key=lambda x: x[1][0]["timestamp"], reverse=True)
        dt = datetime.strptime(month_key, "%Y-%m")
        lines.append("")
        lines.append(f"## {dt.strftime('%B %Y')}")
        lines.append("")

        for session_id, prompts in month_sessions:
            name = make_conversation_name(prompts, session_id)
            start_dt = ts_to_datetime(prompts[0]["timestamp"])
            prompt_count = len([p for p in prompts if not is_slash_command(p["display"].strip())])
            md_path = md_paths.get(session_id, "")
            project_short = Path(prompts[0].get("project", "")).name or "~"
            lines.append(
                f"- [{start_dt.strftime('%m-%d')} {name}]({md_path})"
                f" — {prompt_count} prompts | `{project_short}`"
            )

    lines.append("")
    (vault_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def build_database(sessions: dict[str, list[dict]], md_paths: dict[str, str], db_path: Path):
    """Build SQLite database with FTS5 from parsed sessions."""
    # Remove existing DB for idempotent rebuild
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE conversations (
            session_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            project TEXT,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            prompt_count INTEGER NOT NULL,
            md_path TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES conversations(session_id),
            prompt_text TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            project TEXT,
            seq INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE prompts_fts USING fts5(
            prompt_text,
            content=prompts,
            content_rowid=id
        )
    """)

    for session_id, prompts in sessions.items():
        real_prompts = [p for p in prompts if not is_slash_command(p["display"].strip())]
        if not real_prompts:
            real_prompts = prompts

        name = make_conversation_name(prompts, session_id)
        project = prompts[0].get("project", "")
        start_ts = prompts[0]["timestamp"]
        end_ts = prompts[-1]["timestamp"]
        md_path = md_paths.get(session_id, "")

        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, name, project, start_ts, end_ts, len(real_prompts), md_path),
        )

        for seq, p in enumerate(real_prompts, 1):
            conn.execute(
                "INSERT INTO prompts (session_id, prompt_text, timestamp, project, seq) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, p["display"].strip(), p["timestamp"], p.get("project", ""), seq),
            )

    # Rebuild FTS index
    conn.execute("INSERT INTO prompts_fts(prompts_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()


def main():
    """Entry point for promptvault-sync."""
    history_path = Path(os.environ.get("PROMPTVAULT_HISTORY", str(DEFAULT_HISTORY_PATH)))
    output_dir = Path(os.environ.get("PROMPTVAULT_OUTPUT", str(DEFAULT_OUTPUT_DIR)))

    if not history_path.exists():
        print(f"Error: history file not found at {history_path}", file=sys.stderr)
        sys.exit(1)

    vault_dir = output_dir / "vault"
    db_path = output_dir / "prompts.db"

    # Clean vault for idempotent rebuild
    if vault_dir.exists():
        shutil.rmtree(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading history from {history_path}...")
    sessions = parse_history(history_path)

    print(f"Found {len(sessions)} conversations, {sum(len(p) for p in sessions.values())} prompts")

    print("Generating markdown vault...")
    md_paths = generate_vault(sessions, vault_dir)

    print("Generating vault index...")
    generate_index(sessions, md_paths, vault_dir)

    print("Building SQLite database...")
    build_database(sessions, md_paths, db_path)

    print(f"\nDone! Vault: {vault_dir}")
    print(f"Database: {db_path}")
    print(f"Index: {vault_dir / '_index.md'}")


if __name__ == "__main__":
    main()
