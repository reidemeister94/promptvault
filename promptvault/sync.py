"""Sync engine: reads ~/.claude/history.jsonl and generates markdown vault + SQLite DB."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
DEFAULT_OUTPUT_DIR = Path.home() / ".claude" / "prompt-library"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_PASTE_CACHE_DIR = Path.home() / ".claude" / "paste-cache"
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

SOURCE_CLAUDE = "claude-code"
SOURCE_CODEX = "codex"
PASTE_MARKER = re.compile(r"\[Pasted text #(\d+)[^\]]*\]")

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


def load_session_summaries(projects_dir: Path) -> dict[str, str]:
    """Load auto-generated session summaries from Claude Code's sessions-index.json files."""
    summaries: dict[str, str] = {}
    if not projects_dir.exists():
        return summaries
    for idx_file in projects_dir.glob("*/sessions-index.json"):
        try:
            data = json.loads(idx_file.read_text())
            for entry in data.get("entries", []):
                sid = entry.get("sessionId", "")
                summary = entry.get("summary", "")
                if sid and summary:
                    summaries[sid] = summary
        except (json.JSONDecodeError, OSError):
            continue
    return summaries


def _archive_paste(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            temp_path = Path(temp.name)
            temp.write(content)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _read_archived_paste(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _resolve_paste_content(
    paste_info: dict, paste_cache_dir: Path, paste_archive_dir: Path | None = None
) -> str:
    """Resolve a paste and retain any cache file needed for future syncs."""
    content = paste_info.get("content", "")
    if content:
        return content.strip()
    content_hash = paste_info.get("contentHash")
    if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-fA-F]+", content_hash):
        return ""

    filename = f"{content_hash}.txt"
    archived = paste_archive_dir / filename if paste_archive_dir is not None else None
    saved = _read_archived_paste(archived)
    if saved:
        return saved
    try:
        content = (paste_cache_dir / filename).read_text(encoding="utf-8")
    except OSError:
        # Another sync may have archived the paste before Claude removed it.
        return _read_archived_paste(archived)
    resolved = content.strip()
    if archived is not None and resolved:
        _archive_paste(archived, content)
    return resolved


def resolve_pasted_content(
    entry: dict, paste_cache_dir: Path | None = None, paste_archive_dir: Path | None = None
) -> str:
    """Replace [Pasted text #N ...] placeholders with actual pasted content."""
    display = entry["display"]
    pasted = entry.get("pastedContents", {})
    if not pasted:
        return display

    cache_dir = paste_cache_dir if paste_cache_dir is not None else DEFAULT_PASTE_CACHE_DIR

    def replace_marker(match: re.Match[str]) -> str:
        paste_info = pasted.get(match[1])
        if not isinstance(paste_info, dict):
            return match[0]
        return _resolve_paste_content(paste_info, cache_dir, paste_archive_dir) or match[0]

    return PASTE_MARKER.sub(replace_marker, display)


def parse_history(
    history_path: Path,
    *,
    paste_cache_dir: Path | None = None,
    paste_archive_dir: Path | None = None,
) -> dict[str, list[dict]]:
    """Parse history.jsonl into sessions. Each session is a list of prompt entries."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            entry["display"] = resolve_pasted_content(entry, paste_cache_dir, paste_archive_dir)
            entry["source"] = SOURCE_CLAUDE
            sessions[entry["sessionId"]].append(entry)
    return {sid: _sort_and_dedup(ents) for sid, ents in sessions.items()}


def _sort_and_dedup(entries: list[dict]) -> list[dict]:
    """Sort prompts by timestamp and drop consecutive duplicate texts (double-submits)."""
    entries.sort(key=lambda e: e["timestamp"])
    deduped: list[dict] = []
    for entry in entries:
        if not deduped or entry["display"].strip() != deduped[-1]["display"].strip():
            deduped.append(entry)
    return deduped


def _iso_to_ms(iso: str) -> int | None:
    """Convert an ISO-8601 timestamp (trailing Z or offset) to epoch milliseconds.

    Returns None if unparseable. Fractional seconds are normalized to 6 digits so
    non-microsecond precision (e.g. nanoseconds, which Python 3.10's fromisoformat
    rejects) is tolerated instead of aborting the whole sync.
    """
    text = iso.strip().replace("Z", "+00:00")
    m = re.match(r"(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", text)
    if m:
        base, frac, tz = m.groups()
        if frac is not None:
            text = f"{base}.{(frac + '000000')[:6]}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def parse_codex_sessions(sessions_dir: Path) -> dict[str, list[dict]]:
    """Parse Codex rollout session files into the same session map as parse_history.

    Reads ``sessions_dir/**/rollout-*.jsonl``. User prompts are ``event_msg`` records
    whose ``payload.type == 'user_message'``; the project is ``session_meta.payload.cwd``.
    Files lacking a ``session_meta`` line, and sessions with no user prompt, are skipped;
    malformed lines are ignored so a corrupt tail never loses a whole file.
    """
    sessions: dict[str, list[dict]] = defaultdict(list)
    if not sessions_dir.exists():
        return {}

    for path in sorted(sessions_dir.glob("**/rollout-*.jsonl")):
        session_id = ""
        project = ""
        meta_ts_ms = 0
        entries: list[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload") or {}
                if record.get("type") == "session_meta":
                    session_id = payload.get("session_id", "")
                    project = payload.get("cwd", "")
                    ts = record.get("timestamp")
                    meta_ts_ms = (_iso_to_ms(ts) or 0) if ts else 0
                elif record.get("type") == "event_msg" and payload.get("type") == "user_message":
                    text = payload.get("message", "")
                    if not text:
                        continue
                    ts = record.get("timestamp")
                    ts_ms = _iso_to_ms(ts) if ts else None
                    entries.append(
                        {
                            "display": text,
                            "timestamp": meta_ts_ms if ts_ms is None else ts_ms,
                            "source": SOURCE_CODEX,
                        }
                    )
        if not session_id or not entries:
            continue
        for entry in entries:
            entry["sessionId"] = session_id
            entry["project"] = project
            if not entry["timestamp"]:
                entry["timestamp"] = meta_ts_ms
        sessions[session_id].extend(entries)

    return {sid: _sort_and_dedup(ents) for sid, ents in sessions.items()}


def is_slash_command(prompt: str) -> bool:
    """Check if a prompt is a slash command (should be filtered from vault display)."""
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return False
    # Match known commands or any /word pattern (covers custom commands like /plugin)
    cmd = stripped.split()[0].rstrip()
    return cmd in SLASH_COMMANDS or re.match(r"^/[a-z][\w-]*$", cmd) is not None


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL-safe slug."""
    text = text[:max_length].lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")


def make_conversation_name(prompts: list[dict], session_id: str) -> str:
    """Generate a slug name from the first non-command prompt."""
    for p in prompts:
        display = p["display"].strip()
        if not is_slash_command(display) and len(display) > 2:
            slug = slugify(display)
            if slug:
                return slug
    return f"session-{session_id[:8]}"


def _clean_for_title(text: str) -> str:
    """Strip markers and noise from text for use as a title."""
    text = re.sub(r"\[Image #\d+[^\]]*\]", "", text)
    text = re.sub(r"\[Pasted text #\d+[^\]]*\]", "", text)
    # Remove leading cd/env commands (debug pastes)
    text = re.sub(r"^cd\s+\S+\s*;\s*/usr/bin/env\s+\S+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_display_name(prompts: list[dict], session_id: str, summary: str | None = None) -> str:
    """Generate a human-readable title. Prefers Claude's auto-generated summary."""
    if summary:
        return summary
    for p in prompts:
        display = p["display"].strip()
        if is_slash_command(display) or len(display) <= 2:
            continue
        clean = _clean_for_title(display)
        if len(clean) < 3:
            continue
        title = clean[0].upper() + clean[1:]
        return title[:80] + "..." if len(title) > 80 else title
    return "(no text prompts)"


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
    source = prompts[0].get("source", SOURCE_CLAUDE)

    # Title from first real prompt — clean whitespace
    title_prompt = re.sub(r"\s+", " ", all_prompts[0]["display"]).strip() if all_prompts else name
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
        f"source: {source}",
        "tags:",
        f"  - {source}",
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
        # Strip trailing whitespace per line, then squeeze consecutive blank lines
        text = p["display"].strip()
        text = re.sub(r"[^\S\n]+$", "", text, flags=re.MULTILINE)
        lines.append(re.sub(r"\n{3,}", "\n\n", text))

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


def build_database(
    sessions: dict[str, list[dict]],
    md_paths: dict[str, str],
    db_path: Path,
    summaries: dict[str, str] | None = None,
):
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
            display_name TEXT NOT NULL,
            project TEXT,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            prompt_count INTEGER NOT NULL,
            md_path TEXT,
            source TEXT NOT NULL DEFAULT 'claude-code'
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

        name = make_conversation_name(prompts, session_id)
        summary = (summaries or {}).get(session_id)
        display_name = make_display_name(prompts, session_id, summary)
        project = prompts[0].get("project", "")
        source = prompts[0].get("source", SOURCE_CLAUDE)
        start_ts = prompts[0]["timestamp"]
        end_ts = prompts[-1]["timestamp"]
        md_path = md_paths.get(session_id, "")

        # Use real prompt count (0 if only slash commands)
        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                name,
                display_name,
                project,
                start_ts,
                end_ts,
                len(real_prompts),
                md_path,
                source,
            ),
        )

        # Only index real prompts for search
        store_prompts = real_prompts if real_prompts else prompts
        for seq, p in enumerate(store_prompts, 1):
            conn.execute(
                "INSERT INTO prompts (session_id, prompt_text, timestamp, project, seq) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, p["display"].strip(), p["timestamp"], p.get("project", ""), seq),
            )

    # Rebuild FTS index
    conn.execute("INSERT INTO prompts_fts(prompts_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()


def main(quiet: bool = False):
    """Entry point for promptvault-sync."""
    log = (lambda *a, **kw: None) if quiet else print

    history_path = Path(os.environ.get("PROMPTVAULT_HISTORY", str(DEFAULT_HISTORY_PATH)))
    output_dir = Path(os.environ.get("PROMPTVAULT_OUTPUT", str(DEFAULT_OUTPUT_DIR)))
    codex_dir = Path(os.environ.get("PROMPTVAULT_CODEX_SESSIONS", str(DEFAULT_CODEX_SESSIONS_DIR)))

    has_claude = history_path.exists()
    has_codex = codex_dir.exists()
    if not has_claude and not has_codex:
        if not quiet:
            print(
                f"Error: no history found (Claude: {history_path}, Codex: {codex_dir})",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    vault_dir = output_dir / "vault"
    db_path = output_dir / "prompts.db"

    # Session IDs are disjoint UUIDs across tools, so a plain merge is collision-safe.
    sessions: dict[str, list[dict]] = {}
    if has_claude:
        log(f"Reading Claude history from {history_path}...")
        paste_cache_dir = Path(
            os.environ.get("PROMPTVAULT_PASTE_CACHE", str(DEFAULT_PASTE_CACHE_DIR))
        )
        sessions.update(
            parse_history(
                history_path,
                paste_cache_dir=paste_cache_dir,
                paste_archive_dir=output_dir / "pastes",
            )
        )
    if has_codex:
        log(f"Reading Codex sessions from {codex_dir}...")
        codex_sessions = parse_codex_sessions(codex_dir)
        sessions.update(codex_sessions)
        log(f"Found {len(codex_sessions)} Codex conversations")

    log(f"Found {len(sessions)} conversations, {sum(len(p) for p in sessions.values())} prompts")

    projects_dir = Path(os.environ.get("PROMPTVAULT_PROJECTS", str(DEFAULT_PROJECTS_DIR)))
    summaries = load_session_summaries(projects_dir)
    log(f"Loaded {len(summaries)} session titles from Claude Code")

    # Keep existing outputs until input parsing and paste archival have succeeded.
    if vault_dir.exists():
        shutil.rmtree(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)

    log("Generating markdown vault...")
    md_paths = generate_vault(sessions, vault_dir)

    log("Generating vault index...")
    generate_index(sessions, md_paths, vault_dir)

    log("Building SQLite database...")
    build_database(sessions, md_paths, db_path, summaries)

    log(f"\nDone! Vault: {vault_dir}")
    log(f"Database: {db_path}")
    log(f"Index: {vault_dir / '_index.md'}")


if __name__ == "__main__":
    main()
