"""CLI search over the promptvault SQLite database."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from promptvault import __version__

DEFAULT_DB_PATH = Path.home() / ".claude" / "prompt-library" / "prompts.db"
DEFAULT_VAULT_DIR = Path.home() / ".claude" / "prompt-library" / "vault"

# ANSI colors
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _auto_sync_if_stale(db_path: Path):
    """Run sync if history.jsonl is newer than the DB (or DB doesn't exist)."""
    history_path = Path(
        os.environ.get("PROMPTVAULT_HISTORY", str(Path.home() / ".claude" / "history.jsonl"))
    )
    if not history_path.exists():
        return
    needs_sync = not db_path.exists() or history_path.stat().st_mtime > db_path.stat().st_mtime
    if needs_sync:
        from promptvault.sync import main as sync_main

        print(f"{DIM}Syncing...{RESET}", file=sys.stderr, end=" ", flush=True)
        sync_main(quiet=True)
        print(f"{DIM}done.{RESET}", file=sys.stderr)


def get_db(db_path: Path) -> sqlite3.Connection:
    _auto_sync_if_stale(db_path)
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        print("Run 'promptvault-sync' first to build the database.", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(str(db_path))


def ts_to_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def ts_to_short(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def clean_prompt_text(text: str) -> str:
    """Clean prompt text for display: collapse whitespace, strip markers."""
    text = re.sub(r"\[Pasted text #\d+[^\]]*\]", "", text)
    # Count images before removing
    image_count = len(re.findall(r"\[Image #\d+[^\]]*\]", text))
    text = re.sub(r"\[Image #\d+[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # If only images remain, show a label
    if not text and image_count:
        return f"(image{'s' if image_count > 1 else ''})"
    return text


def truncate(text: str, max_len: int = 120) -> str:
    text = clean_prompt_text(text)
    if not text:
        return "(empty)"
    return text[:max_len] + "..." if len(text) > max_len else text


# ---------------------------------------------------------------------------
# fzf interactive mode
# ---------------------------------------------------------------------------


def has_fzf() -> bool:
    return shutil.which("fzf") is not None


def _fzf_version() -> tuple[int, ...]:
    """Parse fzf version. Returns (0, 0, 0) on failure."""
    try:
        out = subprocess.run(["fzf", "--version"], capture_output=True, text=True).stdout
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", out.strip())
        return tuple(int(x) for x in match.groups()) if match else (0, 0, 0)
    except FileNotFoundError:
        return (0, 0, 0)


def _short_title(text: str, max_words: int = 4) -> str:
    """Shorten a title to max_words, capped at 35 chars."""
    text = clean_prompt_text(text)
    words = text.split()[:max_words]
    title = " ".join(words)
    if len(title) > 35:
        title = title[:33] + ".."
    return title


def _short_project(project: str) -> str:
    """Shorten project path to a readable name. Home dir → ~."""
    if not project:
        return "~"
    name = Path(project).name
    home_name = Path.home().name
    if name == home_name:
        return "~"
    max_len = 20
    if len(name) > max_len:
        return name[: max_len - 1] + "…"
    return name


def _build_conversation_lines(conn: sqlite3.Connection, query: str | None = None) -> list[str]:
    """Build conversation lines for fzf. Format: 'md_path\\tdate  Np  project  title'."""
    if query:
        # Find conversations that contain matching prompts
        session_ids = _fts_session_ids(conn, query)
        if not session_ids:
            return []
        placeholders = ",".join("?" * len(session_ids))
        rows = conn.execute(
            f"""
            SELECT session_id, COALESCE(display_name, name), project, start_ts, end_ts, prompt_count, md_path
            FROM conversations
            WHERE session_id IN ({placeholders})
            ORDER BY start_ts DESC
            """,
            session_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT session_id, COALESCE(display_name, name), project, start_ts, end_ts, prompt_count, md_path
            FROM conversations
            WHERE prompt_count > 0
            ORDER BY start_ts DESC
            """
        ).fetchall()

    lines = []
    for _sid, display_name, project, start_ts, _end_ts, prompt_count, md_path in rows:
        proj = _short_project(project)
        date_str = ts_to_short(start_ts)
        title = _short_title(display_name)
        # Field 1: md_path (hidden), Field 2: visible display, Field 3: full text (hidden, searchable)
        line = f"{md_path}\t{date_str}  {prompt_count:2d}p  {proj:16s}  {title}"
        lines.append(line)
    return lines


def _fts_prepare_query(query: str) -> str:
    """Prepare query for FTS5: add prefix wildcard for partial word matching."""
    words = query.strip().split()
    if not words:
        return query
    # Add * to last word for prefix matching (user is still typing)
    words[-1] = words[-1] + "*"
    return " ".join(words)


def _fts_session_ids(conn: sqlite3.Connection, query: str) -> list[str]:
    """Get unique session IDs matching the FTS query."""
    sql = """
        SELECT DISTINCT p.session_id
        FROM prompts_fts
        JOIN prompts p ON prompts_fts.rowid = p.id
        WHERE prompts_fts MATCH ?
        LIMIT 500
    """
    fts_query = _fts_prepare_query(query)
    try:
        ids = [r[0] for r in conn.execute(sql, (fts_query,)).fetchall()]
        if not ids and " " in query.strip():
            words = query.strip().split()
            or_query = " OR ".join(w + "*" for w in words)
            ids = [r[0] for r in conn.execute(sql, (or_query,)).fetchall()]
        return ids
    except sqlite3.OperationalError:
        return []


def _fzf_preview_script(vault_dir: Path) -> str:
    """Shell command for fzf --preview. Uses {q} to highlight the live query.

    Outputs 3 metadata lines (pinned via ~3 in preview-window) then prompt content.
    """
    # {q} is replaced by fzf with the current query string in real time
    # cat -s squeezes consecutive blank lines into one
    # First 3 lines: title, metadata fields, separator — pinned by ~3
    return (
        f"md_path=$(echo {{}} | cut -f1); "
        f"file='{vault_dir}/'\"$md_path\"; "
        f"q={{q}}; "
        f'if [ ! -f "$file" ]; then echo "File not found"; '
        f"else "
        # Line 1: title from markdown heading
        f"head -20 \"$file\" | grep '^# ' | head -1; "
        # Line 2: key metadata fields on one line
        f"head -20 \"$file\" | grep -E '^\\*\\*(Project|Duration|Prompts)\\*\\*' "
        f"| head -3 | tr '\\n' ' '; echo; "
        # Line 3: separator
        f"echo '---'; "
        # Prompt content with optional query highlighting
        f'if [ -n "$q" ]; then '
        f"sed -n '/^## Prompt/,$p' \"$file\" | cat -s | "
        f"GREP_COLOR='1;33' grep --color=always -i -E \"$q|$\"; "
        f"else "
        f"sed -n '/^## Prompt/,$p' \"$file\" | cat -s; "
        f"fi; "
        f"fi"
    )


def _fzf_copy_script(vault_dir: Path) -> str:
    """Shell command for ctrl-y: copy selected conversation's prompts to clipboard."""
    return (
        f"md_path=$(echo {{}} | cut -f1); "
        f"file='{vault_dir}/'\"$md_path\"; "
        f'if [ -f "$file" ]; then '
        f"sed -n '/^## Prompt/,$p' \"$file\" | pbcopy; "
        f"fi"
    )


def _run_fzf(
    lines: list[str],
    vault_dir: Path,
    db_path: Path | None = None,
    query: str | None = None,
    header: str = "",
    prompt: str = "promptvault> ",
):
    """Run fzf with conversation lines and preview."""
    fzf_ver = _fzf_version()

    # Resolve output dir for search history persistence
    output_dir = Path(
        os.environ.get("PROMPTVAULT_OUTPUT", str(Path.home() / ".claude" / "prompt-library"))
    )

    # Default header: show conversation count as stats
    if not header:
        header = f"{len(lines)} conversations"

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--delimiter=\t",
        "--with-nth=2",  # display only the visible part (after tab)
        "--multi",
        "--preview",
        _fzf_preview_script(vault_dir),
        "--preview-window=right:50%:wrap:~3",  # ~3 pins metadata header lines
        f"--header={header}",
        f"--prompt={prompt}",
        "--no-sort",  # keep our ordering (by date)
        "--height=90%",
        "--layout=reverse",
        "--border=rounded",
        "--color=header:italic:dim,prompt:cyan,pointer:cyan,marker:green",
        "--bind",
        f"ctrl-y:execute-silent({_fzf_copy_script(vault_dir)})+bell",
        "--bind",
        "ctrl-/:toggle-preview",
        # enter opens editor via execute() and returns to fzf afterward
        "--bind",
        f"enter:execute({os.environ.get('EDITOR', 'less')} {vault_dir}/{{1}})",
        "--history",
        str(output_dir / ".search_history"),
    ]

    # Version-gated features: highlight-line, ghost text, footer, tmux popup
    if fzf_ver >= (0, 53, 0):
        fzf_cmd.append("--highlight-line")
    if fzf_ver >= (0, 54, 0):
        fzf_cmd.append("--ghost=Type to search prompts...")
    if fzf_ver >= (0, 53, 0):
        fzf_cmd.append("--footer=enter open | ctrl-y copy | ctrl-/ preview | tab select | esc quit")
    if os.environ.get("TMUX") and fzf_ver >= (0, 38, 0):
        fzf_cmd.extend(["--tmux", "center,80%,60%"])

    # If db_path provided, use FTS search on keystroke instead of fzf's built-in filter
    if db_path:
        pv_bin = shutil.which("promptvault") or f"{sys.executable} -m promptvault.search"
        reload_cmd = f"{pv_bin} --db {db_path} _fzf-lines {{q}}"
        fzf_cmd.extend(
            [
                "--disabled",  # disable built-in filtering
                "--bind",
                f"change:reload({reload_cmd} 2>/dev/null || true)",
            ]
        )

    if query:
        fzf_cmd.extend(["--query", query])

    try:
        subprocess.run(
            fzf_cmd,
            input="\n".join(lines),
            capture_output=True,
            text=True,
        )
        # editor is launched by fzf's execute() binding; Esc exits
    except FileNotFoundError:
        print("fzf not found. Install it: brew install fzf", file=sys.stderr)
        sys.exit(1)


def cmd_search_interactive(
    conn: sqlite3.Connection, query: str | None, vault_dir: Path, db_path: Path | None = None
):
    """Interactive fzf-powered search with conversations."""
    lines = _build_conversation_lines(conn, query)
    if not lines:
        if query:
            print(f"No conversations found for '{query}'")
        else:
            print("No conversations found.")
        return

    _run_fzf(lines, vault_dir, db_path=db_path, query=query)


# ---------------------------------------------------------------------------
# Non-interactive (plain text) mode
# ---------------------------------------------------------------------------


def _fts_search(conn: sqlite3.Connection, query: str, limit: int = 200) -> list:
    """FTS5 search with OR fallback."""
    sql = """
        SELECT p.prompt_text, p.timestamp, p.project, COALESCE(c.display_name, c.name), c.md_path,
               bm25(prompts_fts) AS rank
        FROM prompts_fts
        JOIN prompts p ON prompts_fts.rowid = p.id
        JOIN conversations c ON p.session_id = c.session_id
        WHERE prompts_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    fts_query = _fts_prepare_query(query)
    try:
        rows = conn.execute(sql, (fts_query, limit)).fetchall()
        # Fallback to OR if no results with AND
        if not rows and " " in query.strip():
            words = query.strip().split()
            or_query = " OR ".join(w + "*" for w in words)
            rows = conn.execute(sql, (or_query, limit)).fetchall()
        return rows
    except sqlite3.OperationalError:
        return []


def cmd_search_plain(conn: sqlite3.Connection, query: str, limit: int = 20):
    """Plain text search output (non-interactive)."""
    rows = _fts_search(conn, query, limit)

    if not rows:
        print(f"No results for '{query}'")
        return

    print(f"\n{BOLD}Found {len(rows)} result(s) for '{query}':{RESET}\n")
    for prompt_text, ts, project, conv_name, _md_path, _rank in rows:
        project_short = _short_project(project)
        short_name = conv_name[:50] + "..." if len(conv_name) > 50 else conv_name
        print(f"  {CYAN}{ts_to_str(ts)}{RESET}  {BOLD}{truncate(prompt_text)}{RESET}")
        print(f"  {DIM}{short_name} · {project_short}{RESET}")
        print()


def cmd_search(args: argparse.Namespace, db_path: Path):
    """Search — interactive by default, plain with --no-fzf."""
    conn = get_db(db_path)
    query = args.query if hasattr(args, "query") and args.query else None
    vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
    no_fzf = getattr(args, "no_fzf", False)

    if no_fzf or not sys.stdout.isatty() or not has_fzf():
        if query:
            cmd_search_plain(conn, query, args.limit or 20)
        else:
            print("Provide a search query or install fzf for interactive mode.")
    else:
        cmd_search_interactive(conn, query, vault_dir, db_path)


# ---------------------------------------------------------------------------
# Other commands
# ---------------------------------------------------------------------------


def cmd_recent(args: argparse.Namespace, db_path: Path):
    """Show most recent conversations — interactive with fzf, plain otherwise."""
    conn = get_db(db_path)
    limit = args.count or 20
    vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
    no_fzf = getattr(args, "no_fzf", False)

    if not no_fzf and sys.stdout.isatty() and has_fzf():
        rows = conn.execute(
            """
            SELECT session_id, COALESCE(display_name, name), project, start_ts, end_ts, prompt_count, md_path
            FROM conversations WHERE prompt_count > 0 ORDER BY start_ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        lines = []
        for _sid, display_name, project, start_ts, _end_ts, prompt_count, md_path in rows:
            proj = _short_project(project)
            date_str = ts_to_short(start_ts)
            title = _short_title(display_name)
            lines.append(
                f"{md_path}\t{date_str}  {prompt_count:2d}p  {proj:16s}  {title}\t{display_name}"
            )
        if not lines:
            print("No conversations found.")
            return
        _run_fzf(
            lines,
            vault_dir,
            header="Recent conversations · ↑↓ navigate · enter open",
            prompt="recent> ",
        )
    else:
        rows = conn.execute(
            """
            SELECT p.prompt_text, p.timestamp, p.project, COALESCE(c.display_name, c.name), c.md_path
            FROM prompts p
            JOIN conversations c ON p.session_id = c.session_id
            WHERE p.prompt_text NOT GLOB '/[a-z]*'
              AND p.prompt_text NOT GLOB '[[]Image #[0-9]*[]]'
              AND LENGTH(TRIM(p.prompt_text)) > 0
            ORDER BY p.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        print(f"\n{BOLD}Last {len(rows)} prompts:{RESET}\n")
        for prompt_text, ts, project, conv_name, _md_path in rows:
            project_short = _short_project(project)
            short_name = conv_name[:50] + "..." if len(conv_name) > 50 else conv_name
            print(f"  {CYAN}{ts_to_str(ts)}{RESET}  {BOLD}{truncate(prompt_text)}{RESET}")
            print(f"  {DIM}{short_name} · {project_short}{RESET}")
            print()


def cmd_list(args: argparse.Namespace, db_path: Path):
    """List conversations — interactive with fzf, plain otherwise."""
    conn = get_db(db_path)
    vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
    no_fzf = getattr(args, "no_fzf", False)

    sql = "SELECT session_id, COALESCE(display_name, name), project, start_ts, end_ts, prompt_count, md_path FROM conversations"
    params: list = []
    conditions: list[str] = ["prompt_count > 0"]

    if args.date:
        try:
            dt = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
        start_of_day = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_of_day = start_of_day + 86400000
        conditions.append("start_ts >= ? AND start_ts < ?")
        params.extend([start_of_day, end_of_day])

    if args.project:
        conditions.append("project LIKE ?")
        params.append(f"%{args.project}%")

    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY start_ts DESC"

    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("No conversations found.")
        return

    if not no_fzf and sys.stdout.isatty() and has_fzf():
        lines = []
        for _sid, display_name, project, start_ts, _end_ts, prompt_count, md_path in rows:
            proj = _short_project(project)
            date_str = ts_to_short(start_ts)
            title = _short_title(display_name)
            lines.append(
                f"{md_path}\t{date_str}  {prompt_count:2d}p  {proj:16s}  {title}\t{display_name}"
            )
        _run_fzf(
            lines, vault_dir, header="Conversations · ↑↓ navigate · enter open", prompt="list> "
        )
    else:
        print(f"\n{BOLD}{len(rows)} conversation(s):{RESET}\n")
        for _sid, display_name, project, start_ts, end_ts, prompt_count, md_path in rows:
            project_short = _short_project(project)
            start = ts_to_str(start_ts)
            end_time = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime("%H:%M")
            print(
                f"  {CYAN}{start}–{end_time}{RESET}  "
                f"{BOLD}{display_name}{RESET}  "
                f"{GREEN}{prompt_count}p{RESET}  "
                f"{DIM}{project_short}{RESET}"
            )
            print()


def cmd_stats(args: argparse.Namespace, db_path: Path):
    """Show vault statistics."""
    conn = get_db(db_path)

    conv_count = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE prompt_count > 0"
    ).fetchone()[0]
    prompt_count = conn.execute("SELECT SUM(prompt_count) FROM conversations").fetchone()[0] or 0
    project_count = conn.execute(
        "SELECT COUNT(DISTINCT project) FROM conversations WHERE prompt_count > 0"
    ).fetchone()[0]

    first_ts = conn.execute("SELECT MIN(start_ts) FROM conversations").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(end_ts) FROM conversations").fetchone()[0]

    top_projects = conn.execute(
        """
        SELECT project, COUNT(*) as cnt
        FROM conversations
        GROUP BY project
        ORDER BY cnt DESC
        LIMIT 5
        """
    ).fetchall()

    print(f"\n{BOLD}Prompt Vault{RESET} {DIM}v{__version__}{RESET}\n")
    print(f"  Conversations:  {CYAN}{conv_count}{RESET}")
    print(f"  Prompts:        {CYAN}{prompt_count}{RESET}")
    print(f"  Projects:       {CYAN}{project_count}{RESET}")
    if first_ts and last_ts:
        print(f"  Date range:     {CYAN}{ts_to_str(first_ts)} — {ts_to_str(last_ts)}{RESET}")

    if top_projects:
        print(f"\n  {BOLD}Top projects:{RESET}")
        max_cnt = top_projects[0][1] if top_projects else 1
        max_bar = 30
        for project, cnt in top_projects:
            project_short = _short_project(project)
            bar_len = max(1, int(cnt / max_cnt * max_bar))
            bar = YELLOW + "█" * bar_len + RESET
            print(f"    {project_short:22s} {bar} {cnt}")

    print()


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptvault",
        description="Search your Claude Code prompt history",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to prompts.db (default: ~/.claude/prompt-library/prompts.db)",
    )
    parser.add_argument(
        "--no-fzf",
        action="store_true",
        help="Disable interactive fzf mode (plain text output)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # search
    search_p = subparsers.add_parser("search", help="Full-text search prompts (interactive)")
    search_p.add_argument("query", nargs="?", default=None, help="Search query (optional with fzf)")
    search_p.add_argument("-n", "--limit", type=int, default=20, help="Max results (plain mode)")
    search_p.add_argument("--no-fzf", action="store_true", help="Disable interactive fzf mode")

    # recent
    recent_p = subparsers.add_parser("recent", help="Show recent prompts")
    recent_p.add_argument("count", nargs="?", type=int, default=10, help="Number of prompts")
    recent_p.add_argument("--no-fzf", action="store_true", help="Disable fzf")

    # list
    list_p = subparsers.add_parser("list", help="List conversations")
    list_p.add_argument("--date", help="Filter by date (YYYY-MM-DD)")
    list_p.add_argument("--project", help="Filter by project name (partial match)")
    list_p.add_argument("-n", "--limit", type=int, help="Max results")
    list_p.add_argument("--no-fzf", action="store_true", help="Disable fzf")

    # stats
    subparsers.add_parser("stats", help="Show vault statistics")

    # hidden: used by fzf reload
    fzf_p = subparsers.add_parser("_fzf-lines")
    fzf_p.add_argument("query", nargs="?", default=None)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    db_path = args.db or Path(os.environ.get("PROMPTVAULT_DB", str(DEFAULT_DB_PATH)))

    # Propagate global --no-fzf to subcommand
    if getattr(args, "no_fzf", False):
        pass  # already set

    # Hidden: fast line output for fzf reload (skip auto-sync for speed)
    if args.command == "_fzf-lines":
        conn = sqlite3.connect(str(db_path))
        q = args.query if args.query and args.query.strip() else None
        lines = _build_conversation_lines(conn, q)
        sys.stdout.write("\n".join(lines) + "\n" if lines else "")
        return

    commands = {
        "search": cmd_search,
        "recent": cmd_recent,
        "list": cmd_list,
        "stats": cmd_stats,
    }

    if args.command in commands:
        commands[args.command](args, db_path)
    else:
        # No subcommand → launch interactive browse or show recent
        if has_fzf() and sys.stdout.isatty() and not getattr(args, "no_fzf", False):
            conn = get_db(db_path)
            vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
            cmd_search_interactive(conn, None, vault_dir, db_path)
        else:
            # Fallback: show recent conversations in plain mode
            args.count = 15
            args.no_fzf = True
            cmd_recent(args, db_path)


if __name__ == "__main__":
    main()
