"""The session board — what the other sessions are doing, in ~800 tokens.

This is the one view no surveyed tool produces. The closest thing in the field
instructs the agent to run `git status` on a file and read someone else's committed
markdown; the only tool that built a presence protocol never reads its own heartbeat,
so dead agents are reported alive forever.

Selection rules, each one a correction of an observed failure:

* Scoped to THIS repository and branch. A well-known plugin keys memory per worktree
  path, so siblings are mutually invisible while unrelated projects share one store.
* Open items are age-gated. Injecting the previous session's "next steps" with no age
  check means session B is handed session A's plan verbatim, whether it is twenty
  minutes or eight months old.
* Hard character cap with the health footer written LAST and never truncated. A board
  that silently shrinks looks identical to a board with nothing to report.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import store
from .gitctx import GitContext
from .sessions import SessionRecord

# Roughly 800 tokens. The budget exists because this is injected once per session and
# again after every compaction, and because subagent spawns re-pay a related cost.
BOARD_CHAR_BUDGET = 3_200

# An open item older than this is not context, it is archaeology.
OPEN_ITEM_MAX_AGE_SECONDS = 14 * 24 * 3600

OPEN_ITEMS_FILE = "open-items.jsonl"
MAX_OPEN_ITEMS = 5


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def open_items(ctx: GitContext, branch: str | None = None) -> list[dict[str, Any]]:
    """Recent, un-closed, age-gated items for this branch.

    Append-only log: a later record with the same id supersedes an earlier one, and a
    record marked closed removes it. Nothing is ever edited in place, so parallel
    sessions never contend.
    """
    now = time.time()
    latest: dict[str, dict[str, Any]] = {}
    for rec in store.read_jsonl(store.tier_b(ctx, OPEN_ITEMS_FILE)):
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        latest[str(rec["id"])] = rec

    out = []
    for rec in latest.values():
        if rec.get("closed"):
            continue
        if branch and rec.get("branch") and rec["branch"] != branch:
            continue
        created = float(rec.get("created_at", 0))
        if now - created > OPEN_ITEM_MAX_AGE_SECONDS:
            continue
        out.append(rec)

    out.sort(key=lambda r: float(r.get("created_at", 0)), reverse=True)
    return out[:MAX_OPEN_ITEMS]


def add_open_item(ctx: GitContext, item_id: str, text: str, branch: str, session_id: str) -> None:
    store.append_jsonl(
        store.tier_b(ctx, OPEN_ITEMS_FILE),
        {
            "id": item_id,
            "text": text[:280],
            "branch": branch,
            "session_id": session_id,
            "created_at": time.time(),
            "closed": False,
        },
    )


def close_open_item(ctx: GitContext, item_id: str) -> None:
    store.append_jsonl(
        store.tier_b(ctx, OPEN_ITEMS_FILE),
        {"id": item_id, "closed": True, "created_at": time.time()},
    )


def health_line(ctx: GitContext, live_count: int, reaped: int) -> str:
    """Counts, sizes and the resolved repo key.

    Not one surveyed tool prints this, which is why a dead memory system looks
    exactly like a quiet one. It costs a single line.
    """
    root = store.tier_b(ctx)
    try:
        size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    except OSError:
        size = 0
    items = len(open_items(ctx))
    return (
        f"health: {live_count} live session(s), {reaped} reaped, {items} open item(s), "
        f"state {size / 1024:.0f}KB at {root}"
    )


def render(
    ctx: GitContext,
    me: SessionRecord,
    others: list[SessionRecord],
    reaped: int,
    leases: dict[str, list[str]] | None = None,
    budget: int = BOARD_CHAR_BUDGET,
) -> str:
    """Build the board, health footer last and guaranteed present."""
    leases = leases or {}
    now = time.time()
    lines: list[str] = []

    lines.append(f"repo {ctx.worktree_root.name} | branch {me.branch} | baseline {me.baseline_commit[:12] or 'unborn'}")

    if others:
        lines.append("")
        lines.append(f"OTHER LIVE SESSIONS ({len(others)}) — do not edit files they hold:")
        for rec in sorted(others, key=lambda r: r.heartbeat_at, reverse=True):
            held = leases.get(rec.session_id, [])
            touched = ", ".join(rec.last_touched[:3]) or "nothing yet"
            lines.append(
                f"  - {rec.session_id[:8]} on {rec.branch} "
                f"[{Path(rec.worktree).name}] active {_age(now - rec.heartbeat_at)}"
            )
            lines.append(f"      touched: {touched}")
            if held:
                lines.append(f"      holds: {', '.join(held[:5])}")
            if rec.task_statement:
                lines.append(f"      task: {rec.task_statement[:120]}")
    else:
        lines.append("")
        lines.append("OTHER LIVE SESSIONS: none. This session is alone on the repository.")

    items = open_items(ctx, branch=me.branch)
    if items:
        lines.append("")
        lines.append("OPEN ITEMS on this branch:")
        for item in items:
            lines.append(f"  - [{item['id'][:8]}] {item.get('text', '')[:160]}")

    footer = health_line(ctx, len(others) + 1, reaped)

    body = "\n".join(lines)
    room = budget - len(footer) - 2
    if len(body) > room:
        body = body[: max(0, room - 24)] + "\n<elided reason=\"budget\" />"
    return f"{body}\n\n{footer}"
