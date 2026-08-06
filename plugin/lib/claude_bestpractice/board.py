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

import hashlib
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
    out = []
    for rec in _latest_by_id(ctx).values():
        if rec.get("closed"):
            continue
        if branch and rec.get("branch") and rec["branch"] != branch:
            continue
        if now - _last_seen(rec) > OPEN_ITEM_MAX_AGE_SECONDS:
            continue
        out.append(rec)

    out.sort(key=_last_seen, reverse=True)
    out = out[:MAX_OPEN_ITEMS]
    return provenance.annotate(ctx, out) if with_provenance else out


def _latest_by_id(ctx: GitContext) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for rec in store.read_jsonl(store.tier_b(ctx, OPEN_ITEMS_FILE)):
        if isinstance(rec, dict) and rec.get("id"):
            latest[str(rec["id"])] = rec
    return latest


def _last_seen(rec: dict[str, Any]) -> float:
    """When this item was last asserted, not when it was first written.

    Age-gating on first sight retired a finding that is still being re-derived from the
    code every time it is reviewed, which is the opposite of what the gate is for.
    """
    return float(rec.get("last_seen_at") or rec.get("created_at") or 0)


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

    Re-sighting an item that is already open counts it rather than filing it again. The
    caller's `item_id` carries a timestamp, so identical findings could never collide by
    construction: measured on a live repository, `open-items.jsonl` held 70 entries and 4
    distinct texts, one review finding stored 34 times. That is not untidiness — the
    board asserts each copy separately, each has to be retired separately when its
    subject moves, and the four rows that said something new were unfindable among the
    repeats.
    """
    now = time.time()
    paths = list(subject_paths or [])
    key = _item_key(text, branch, paths)
    prior = _open_with_key(ctx, key)
    record = {
        "id": prior["id"] if prior else item_id,
        "key": key,
        "text": text[:280],
        "branch": branch,
        "session_id": session_id,
        "created_at": float(prior["created_at"]) if prior else now,
        "last_seen_at": now,
        "seen": int(prior.get("seen", 1)) + 1 if prior else 1,
        "closed": False,
        # Re-stamped on every sighting, not carried over. The claim was just re-derived
        # from what the files hold now, so pinning it to the content it was first seen
        # against would suppress a finding that is currently, demonstrably true.
        "subject_paths": provenance.stamp(ctx, paths),
    }
    store.append_jsonl(store.tier_b(ctx, OPEN_ITEMS_FILE), record)


def _item_key(text: str, branch: str, paths: list[str]) -> str:
    """What makes two sightings the same item: the claim, where, and about what."""
    payload = "\x00".join([" ".join(text.split())[:280].lower(), branch, *sorted(paths)])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


def _open_with_key(ctx: GitContext, key: str) -> dict[str, Any] | None:
    for rec in _latest_by_id(ctx).values():
        if rec.get("key") == key and not rec.get("closed") and rec.get("created_at"):
            return rec
    return None


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


def _alerts(ctx: GitContext) -> list[str]:
    """State that must be seen before anything else, or it gets made worse.

    A Stop block lasts exactly the turn it fires in; these are committed or on-disk, so
    they follow the branch into every worktree and every future session. And an
    unfinished merge leaves a tree that reads as normal while half of it is conflict
    markers — a session that is not told will commit them.
    """
    from . import delivery, pullrequest, upgrade

    return [
        line for line in (
            red_suite_line(ctx).strip(),
            delivery.merge_state(ctx).render(),
            # A pull request nobody comes back to is the one piece of state that looks
            # finished from inside the session that made it. It follows the repository,
            # not the session, so every later session is told until it is merged or closed.
            pullrequest.line(ctx),
            # A released version cannot be withdrawn, so the only place a known-bad one
            # can be named is inside the copy that is running. Empty for every version
            # that is not on the list, which is nearly all of them.
            upgrade.known_bad(),
        ) if line
    ]


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

    lines.extend(_alerts(ctx))

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
            # The repeat count replaces the repeats. It is also the more useful signal:
            # a finding re-derived thirty times is one that thirty reviews agreed on.
            seen = int(item.get("seen", 1))
            again = f" (seen {seen}×, first {_age(now - float(item.get('created_at', now)))})" if seen > 1 else ""
            lines.append(f"  - [{item['id'][:8]}] {item.get('text', '')[:160]}{again}")

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


DISMISSED = "review-dismissed.json"


def dismiss(ctx: GitContext, detector: str, path: str) -> None:
    """Record that the founder judged this finding false, for good.

    TIER A, so the judgement is committed and travels with the repository. It is about the
    code, not about one clone, and re-deciding it in every worktree is how a decision
    becomes noise.

    Keyed on detector and path rather than on the item id. A review writes a new id every
    run, so closing an item retires it until the next review and no further — which is why
    two false findings blocked a merge permanently with nothing to fix (#75).
    """
    from . import store

    current = store.read_json(store.tier_a(ctx, DISMISSED), default={}) or {}
    entries = set(current.get("findings") or [])
    entries.add(f"{detector}:{path}")
    store.write_json(store.tier_a(ctx, DISMISSED), {"findings": sorted(entries)}, mode=0o644)


def dismissed(ctx: GitContext) -> set[str]:
    """`detector:path` pairs the founder has ruled out."""
    from . import store

    current = store.read_json(store.tier_a(ctx, DISMISSED), default={}) or {}
    return {str(entry) for entry in (current.get("findings") or [])}
