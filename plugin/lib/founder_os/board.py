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

from . import provenance, store
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


def open_items(
    ctx: GitContext, branch: str | None = None, with_provenance: bool = True
) -> list[dict[str, Any]]:
    """Recent, un-closed, age-gated items for this branch, tagged with provenance.

    Append-only log: a later record with the same id supersedes an earlier one, and a
    record marked closed removes it. Nothing is ever edited in place, so parallel
    sessions never contend.

    Age gating matters more than it looks. A widely-used memory plugin re-injects the
    previous session's "next steps" with no age check at all, so a session is handed a
    plan that may be eight months old — and under parallel sessions, handed another
    session's plan entirely.
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
    out = out[:MAX_OPEN_ITEMS]
    return provenance.annotate(ctx, out) if with_provenance else out


def add_open_item(
    ctx: GitContext,
    item_id: str,
    text: str,
    branch: str,
    session_id: str,
    subject_paths: list[str] | None = None,
) -> None:
    """Record an item, stamped with the content hashes of what it describes.

    The stamp is what lets a later session tell a still-true item from one whose
    subject has been rewritten underneath it.
    """
    store.append_jsonl(
        store.tier_b(ctx, OPEN_ITEMS_FILE),
        {
            "id": item_id,
            "text": text[:280],
            "branch": branch,
            "session_id": session_id,
            "created_at": time.time(),
            "closed": False,
            "subject_paths": provenance.stamp(ctx, subject_paths or []),
        },
    )


def close_open_item(ctx: GitContext, item_id: str) -> None:
    store.append_jsonl(
        store.tier_b(ctx, OPEN_ITEMS_FILE),
        {"id": item_id, "closed": True, "created_at": time.time()},
    )


def red_suite_line(ctx: GitContext) -> str:
    """A failing suite, carried into every session until it is green.

    Placed above everything else deliberately. A block only lasts the turn it fires in;
    this is the part that means a broken test is never quietly left behind.
    """
    from . import evidence

    line = evidence.red_line(ctx)
    return f"\n{line}\n" if line else ""


def health_line(ctx: GitContext, live_count: int, reaped: int) -> str:
    """Counts, sizes, provenance and the resolved repo key.

    Not one surveyed tool prints this, which is why a dead memory system looks exactly
    like a quiet one. It costs a single line, and it is the difference between "there
    is nothing to report" and "this stopped working three days ago".
    """
    root = store.tier_b(ctx)
    try:
        size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    except OSError:
        size = 0
    items = open_items(ctx)
    counts = provenance.summarize(items)
    stale = counts.get(provenance.SUSPECT, 0) + counts.get(provenance.GONE, 0)
    stale_note = f", {stale} stale (suppressed)" if stale else ""
    return (
        f"health: {live_count} live session(s), {reaped} reaped, "
        f"{counts.get(provenance.FRESH, 0)} open item(s){stale_note}, "
        f"state {size / 1024:.0f}KB at {root}"
    )


def _sessions_block(others: list[SessionRecord], leases: dict, now: float) -> list[str]:
    """Who else is live, what they hold, and what they are doing."""
    if not others:
        return ["OTHER LIVE SESSIONS: none. This session is alone on the repository."]

    out = [f"OTHER LIVE SESSIONS ({len(others)}) — do not edit files they hold:"]
    for rec in sorted(others, key=lambda r: r.heartbeat_at, reverse=True):
        out.append(
            f"  - {rec.session_id[:8]} on {rec.branch} "
            f"[{Path(rec.worktree).name}] active {_age(now - rec.heartbeat_at)}"
        )
        out.append(f"      touched: {', '.join(rec.last_touched[:3]) or 'nothing yet'}")
        held = leases.get(rec.session_id, [])
        if held:
            out.append(f"      holds: {', '.join(held[:5])}")
        if rec.task_statement:
            out.append(f"      task: {rec.task_statement[:120]}")
    return out


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

    # A Stop block lasts exactly the turn it fires in. This is committed state, so a
    # broken suite follows the branch into every worktree and every future session until
    # it is green again — which is the difference between refusing a finish and never
    # letting the failure be forgotten.
    red = red_suite_line(ctx)
    if red:
        lines.append(red.strip())

    # Before anything else this session might redo: dead ends on the files it is about
    # to touch. Keyed on subject, so a billing dead end never reaches a landing-page turn.
    from . import attempts

    tried = attempts.render_for_board(ctx, me.last_touched + me.task_paths)
    if tried:
        lines.append("")
        lines.append(tried)

    lines.append("")
    lines.extend(_sessions_block(others, leases, now))

    # Suppressed, not deleted. A claim whose subject was rewritten underneath it is
    # usually still mostly right, and stale context is measurably worse than none —
    # so it stops being asserted, and the count in the health line says it exists.
    items = [i for i in open_items(ctx, branch=me.branch) if i.get("provenance") == provenance.FRESH]
    if items:
        lines.append("")
        lines.append("OPEN ITEMS on this branch:")
        for item in items:
            lines.append(f"  - [{item['id'][:8]}] {item.get('text', '')[:160]}")

    from . import plan

    ledger = plan.render_for_board(ctx)
    if ledger:
        lines.append("")
        lines.append(ledger)

    footer = health_line(ctx, len(others) + 1, reaped)

    body = "\n".join(lines)
    room = budget - len(footer) - 2
    if len(body) > room:
        body = body[: max(0, room - 24)] + "\n<elided reason=\"budget\" />"
    return f"{body}\n\n{footer}"
