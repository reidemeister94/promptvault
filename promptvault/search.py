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

DEFAULT_DB_PATH = Path.home() / ".claude" / "prompt-library" / "prompts.db"
DEFAULT_VAULT_DIR = Path.home() / ".claude" / "prompt-library" / "vault"

# ANSI colors
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def get_db(db_path: Path) -> sqlite3.Connection:
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
    """Clean prompt text for display: collapse whitespace, strip pasted-text markers."""
    text = re.sub(r"\[Pasted text #\d+[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, max_len: int = 120) -> str:
    text = clean_prompt_text(text)
    return text[:max_len] + "..." if len(text) > max_len else text


# ---------------------------------------------------------------------------
# fzf interactive mode
# ---------------------------------------------------------------------------


def has_fzf() -> bool:
    return shutil.which("fzf") is not None


def _build_fzf_lines(conn: sqlite3.Connection, query: str | None = None) -> list[str]:
    """Build lines for fzf input. Each line: 'md_path\tdate  project  prompt_preview'."""
    if query:
        # FTS search
        rows = _fts_search(conn, query, limit=200)
    else:
        # All prompts, most recent first
        rows = conn.execute(
            """
            SELECT p.prompt_text, p.timestamp, p.project, c.name, c.md_path
            FROM prompts p
            JOIN conversations c ON p.session_id = c.session_id
            ORDER BY p.timestamp DESC
            LIMIT 5000
            """
        ).fetchall()
        # Reshape to match _fts_search output (add dummy rank)
        rows = [(r[0], r[1], r[2], r[3], r[4], 0) for r in rows]

    lines = []
    for prompt_text, ts, project, conv_name, md_path, _rank in rows:
        project_short = Path(project).name if project else "~"
        date_str = ts_to_short(ts)
        prompt_clean = clean_prompt_text(prompt_text)[:150]
        # Tab-separated: md_path is hidden field for preview, rest is display
        line = f"{md_path}\t{date_str}  {project_short:24s}  {prompt_clean}"
        lines.append(line)
    return lines


def _fzf_preview_script(vault_dir: Path) -> str:
    """Shell command for fzf --preview. Reads the md file for the selected line."""
    return (
        f"md_path=$(echo {{}} | cut -f1); "
        f"file='{vault_dir}/'\"$md_path\"; "
        f'if [ -f "$file" ]; then cat "$file"; else echo \'File not found\'; fi'
    )


def cmd_search_interactive(conn: sqlite3.Connection, query: str | None, vault_dir: Path):
    """Interactive fzf-powered search."""
    lines = _build_fzf_lines(conn, query)
    if not lines:
        print("No prompts found.")
        return

    fzf_input = "\n".join(lines)

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--delimiter=\t",
        "--with-nth=2",  # display only the visible part (after tab)
        "--preview",
        _fzf_preview_script(vault_dir),
        "--preview-window=right:50%:wrap",
        "--header=↑↓ navigate · enter open · esc quit · type to filter",
        "--prompt=promptvault> ",
        "--no-sort" if query else "--sort",  # keep FTS ranking if query
        "--height=90%",
        "--layout=reverse",
        "--border=rounded",
        "--color=header:italic:dim,prompt:cyan,pointer:cyan,marker:green",
    ]

    if query:
        fzf_cmd.extend(["--query", query])

    try:
        result = subprocess.run(
            fzf_cmd,
            input=fzf_input,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            selected = result.stdout.strip()
            md_path = selected.split("\t")[0]
            full_path = vault_dir / md_path
            if full_path.exists():
                # Open in $EDITOR or less
                editor = os.environ.get("EDITOR", "less")
                subprocess.run([editor, str(full_path)])
            else:
                print(f"File not found: {full_path}")
    except FileNotFoundError:
        print("fzf not found. Install it: brew install fzf", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Non-interactive (plain text) mode
# ---------------------------------------------------------------------------


def _fts_search(conn: sqlite3.Connection, query: str, limit: int = 200) -> list:
    """FTS5 search with OR fallback."""
    sql = """
        SELECT p.prompt_text, p.timestamp, p.project, c.name, c.md_path,
               bm25(prompts_fts) AS rank
        FROM prompts_fts
        JOIN prompts p ON prompts_fts.rowid = p.id
        JOIN conversations c ON p.session_id = c.session_id
        WHERE prompts_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, (query, limit)).fetchall()
        # Fallback to OR if no results with AND
        if not rows and " " in query.strip():
            words = query.strip().split()
            or_query = " OR ".join(words)
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
    for prompt_text, ts, project, conv_name, md_path, _rank in rows:
        project_short = Path(project).name if project else "~"
        print(f"  {CYAN}{ts_to_str(ts)}{RESET}  {BOLD}{truncate(prompt_text)}{RESET}")
        print(f"  {DIM}{conv_name} | {project_short} | {md_path}{RESET}")
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
        cmd_search_interactive(conn, query, vault_dir)


# ---------------------------------------------------------------------------
# Other commands
# ---------------------------------------------------------------------------


def cmd_recent(args: argparse.Namespace, db_path: Path):
    """Show most recent prompts — interactive with fzf, plain otherwise."""
    conn = get_db(db_path)
    limit = args.count or 10
    vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
    no_fzf = getattr(args, "no_fzf", False)

    if not no_fzf and sys.stdout.isatty() and has_fzf():
        # Interactive: show recent prompts in fzf
        rows = conn.execute(
            """
            SELECT p.prompt_text, p.timestamp, p.project, c.name, c.md_path
            FROM prompts p
            JOIN conversations c ON p.session_id = c.session_id
            ORDER BY p.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        lines = []
        for prompt_text, ts, project, conv_name, md_path in rows:
            project_short = Path(project).name if project else "~"
            date_str = ts_to_short(ts)
            prompt_clean = clean_prompt_text(prompt_text)[:150]
            lines.append(f"{md_path}\t{date_str}  {project_short:24s}  {prompt_clean}")

        if not lines:
            print("No prompts found.")
            return

        fzf_cmd = [
            "fzf",
            "--ansi",
            "--delimiter=\t",
            "--with-nth=2",
            "--preview",
            _fzf_preview_script(vault_dir),
            "--preview-window=right:50%:wrap",
            "--header=Recent prompts · ↑↓ navigate · enter open · esc quit",
            "--prompt=recent> ",
            "--no-sort",
            "--height=90%",
            "--layout=reverse",
            "--border=rounded",
            "--color=header:italic:dim,prompt:cyan,pointer:cyan,marker:green",
        ]

        result = subprocess.run(fzf_cmd, input="\n".join(lines), capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            md_path = result.stdout.strip().split("\t")[0]
            full_path = vault_dir / md_path
            if full_path.exists():
                editor = os.environ.get("EDITOR", "less")
                subprocess.run([editor, str(full_path)])
    else:
        rows = conn.execute(
            """
            SELECT p.prompt_text, p.timestamp, p.project, c.name, c.md_path
            FROM prompts p
            JOIN conversations c ON p.session_id = c.session_id
            ORDER BY p.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        print(f"\n{BOLD}Last {len(rows)} prompts:{RESET}\n")
        for prompt_text, ts, project, conv_name, md_path in rows:
            project_short = Path(project).name if project else "~"
            print(f"  {CYAN}{ts_to_str(ts)}{RESET}  {BOLD}{truncate(prompt_text)}{RESET}")
            print(f"  {DIM}{conv_name} | {project_short} | {md_path}{RESET}")
            print()


def cmd_list(args: argparse.Namespace, db_path: Path):
    """List conversations — interactive with fzf, plain otherwise."""
    conn = get_db(db_path)
    vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
    no_fzf = getattr(args, "no_fzf", False)

    sql = "SELECT session_id, name, project, start_ts, end_ts, prompt_count, md_path FROM conversations"
    params: list = []
    conditions: list[str] = []

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

    if conditions:
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
        for _sid, name, project, start_ts, end_ts, prompt_count, md_path in rows:
            project_short = Path(project).name if project else "~"
            date_str = ts_to_short(start_ts)
            end_time = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime("%H:%M")
            line = (
                f"{md_path}\t{date_str}-{end_time}  {prompt_count:3d}p  {project_short:24s}  {name}"
            )
            lines.append(line)

        fzf_cmd = [
            "fzf",
            "--ansi",
            "--delimiter=\t",
            "--with-nth=2",
            "--preview",
            _fzf_preview_script(vault_dir),
            "--preview-window=right:50%:wrap",
            "--header=Conversations · ↑↓ navigate · enter open · esc quit",
            "--prompt=list> ",
            "--no-sort",
            "--height=90%",
            "--layout=reverse",
            "--border=rounded",
            "--color=header:italic:dim,prompt:cyan,pointer:cyan,marker:green",
        ]

        result = subprocess.run(fzf_cmd, input="\n".join(lines), capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            md_path = result.stdout.strip().split("\t")[0]
            full_path = vault_dir / md_path
            if full_path.exists():
                editor = os.environ.get("EDITOR", "less")
                subprocess.run([editor, str(full_path)])
    else:
        print(f"\n{BOLD}{len(rows)} conversation(s):{RESET}\n")
        for _sid, name, project, start_ts, end_ts, prompt_count, md_path in rows:
            project_short = Path(project).name if project else "~"
            start = ts_to_str(start_ts)
            end_time = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).strftime("%H:%M")
            print(
                f"  {CYAN}{start}-{end_time}{RESET}  "
                f"{BOLD}{name}{RESET}  "
                f"{GREEN}{prompt_count}p{RESET}  "
                f"{DIM}{project_short}{RESET}"
            )
            if md_path:
                print(f"  {DIM}{md_path}{RESET}")
            print()


def cmd_stats(args: argparse.Namespace, db_path: Path):
    """Show vault statistics."""
    conn = get_db(db_path)

    conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    prompt_count = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    project_count = conn.execute("SELECT COUNT(DISTINCT project) FROM conversations").fetchone()[0]

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

    print(f"\n{BOLD}Prompt Vault Stats{RESET}\n")
    print(f"  Conversations:  {CYAN}{conv_count}{RESET}")
    print(f"  Prompts:        {CYAN}{prompt_count}{RESET}")
    print(f"  Projects:       {CYAN}{project_count}{RESET}")
    if first_ts and last_ts:
        print(f"  Date range:     {CYAN}{ts_to_str(first_ts)} — {ts_to_str(last_ts)}{RESET}")

    if top_projects:
        print(f"\n  {BOLD}Top projects:{RESET}")
        for project, cnt in top_projects:
            project_short = Path(project).name if project else "~"
            bar = YELLOW + "█" * min(cnt, 40) + RESET
            print(f"    {project_short:30s} {bar} {cnt}")

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

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    db_path = args.db or Path(os.environ.get("PROMPTVAULT_DB", str(DEFAULT_DB_PATH)))

    # Propagate global --no-fzf to subcommand
    if getattr(args, "no_fzf", False):
        pass  # already set

    commands = {
        "search": cmd_search,
        "recent": cmd_recent,
        "list": cmd_list,
        "stats": cmd_stats,
    }

    if args.command in commands:
        commands[args.command](args, db_path)
    else:
        # No subcommand → launch interactive search (like ctrl+R)
        if has_fzf() and sys.stdout.isatty():
            conn = get_db(db_path)
            vault_dir = Path(os.environ.get("PROMPTVAULT_VAULT", str(DEFAULT_VAULT_DIR)))
            cmd_search_interactive(conn, None, vault_dir)
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
