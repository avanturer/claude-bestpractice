"""What was tried and did not work — the half of memory every tool throws away.

Everything else here records outcomes: the task is done, the decision is made, the
suite is green. None of that stops the failure this exists for. A session tries
websockets, hits a reconnect problem it cannot solve, reverts, and ends. Three days
later another session reads a clean tree, sees no trace, and tries websockets again.
The knowledge that mattered was destroyed by the revert that was the correct action.

So a failed approach is a first-class record, kept for the same reason a decision is:
it is a historical fact, and the code no longer contains it. Decisions say what we
chose. Attempts say what we already ruled out by trying, which is strictly more
expensive knowledge because it was paid for in a session that produced nothing.

Surfacing is by SUBJECT, not by recency. An attempt is injected when the session is
about to touch the files that attempt touched — otherwise it is archaeology, and this
project's own measurement is that stale injected context is worse than none.

Tier A, one file per attempt, so five worktrees produce five clean adds.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import provenance, store
from .gitctx import GitContext

ATTEMPTS_DIR = "attempts"
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 1_200
MAX_SHOWN = 3

FAILED = "failed"
ABANDONED = "abandoned"
SUPERSEDED = "superseded"
OUTCOMES = (FAILED, ABANDONED, SUPERSEDED)


@dataclass
class Attempt:
    id: str
    title: str
    outcome: str
    why: str
    paths: list[str] = field(default_factory=list)
    branch: str = ""
    session_id: str = ""
    recorded_at: float = 0.0
    path: Path | None = None

    def line(self) -> str:
        return f"[{self.id}] {self.title} — {self.outcome}: {self.why[:160]}"


def attempts_dir(ctx: GitContext) -> Path:
    return store.tier_a(ctx, ATTEMPTS_DIR)


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:6]) or "attempt"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """`key: value` header, then free text. Deliberately not YAML — no dependency."""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    lines = text.splitlines()[1:]
    for index, line in enumerate(lines):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1:])
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, ""


def _parse(path: Path) -> Attempt | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _split_frontmatter(text)
    if not meta.get("title"):
        return None
    return Attempt(
        id=path.name.split("-", 1)[0],
        title=meta.get("title", ""),
        outcome=meta.get("outcome", FAILED),
        why=body.strip()[:MAX_BODY_CHARS],
        paths=[p for p in meta.get("paths", "").split(",") if p.strip()],
        branch=meta.get("branch", ""),
        session_id=meta.get("session", ""),
        recorded_at=float(meta.get("recorded_at") or 0),
        path=path,
    )


def load_all(ctx: GitContext) -> list[Attempt]:
    directory = attempts_dir(ctx)
    if not directory.is_dir():
        return []
    out = [_parse(p) for p in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md"))]
    return [a for a in out if a]


def next_id(ctx: GitContext) -> str:
    highest = 0
    for attempt in load_all(ctx):
        if attempt.id.isdigit():
            highest = max(highest, int(attempt.id))
    return f"{highest + 1:04d}"


def record(
    ctx: GitContext,
    title: str,
    why: str,
    paths: list[str],
    outcome: str = FAILED,
    branch: str = "",
    session_id: str = "",
) -> Attempt | None:
    """File a failed approach. Returns None when an equivalent one is already on record.

    Deduplicated on (title, overlapping paths) rather than on exact text, because the
    same dead end gets described slightly differently every time it is hit, and a
    ledger that accumulates near-duplicates is one nobody reads.
    """
    if outcome not in OUTCOMES:
        outcome = FAILED
    subject = sorted({p for p in paths if p})[:20]

    for existing in load_all(ctx):
        same_title = existing.title.strip().lower() == title.strip().lower()
        if same_title and (not subject or set(existing.paths) & set(subject)):
            return None

    with store.file_lock(store.tier_b(ctx, "attempts-alloc.lock")):
        attempt_id = next_id(ctx)
        path = attempts_dir(ctx) / f"{attempt_id}-{_slug(title)}.md"
        stamped = provenance.stamp(ctx, subject)
        rendered = "\n".join(
            [
                "---",
                f"title: {title[:MAX_TITLE_CHARS]}",
                f"outcome: {outcome}",
                f"paths: {', '.join(subject)}",
                f"branch: {branch}",
                f"session: {session_id}",
                f"recorded_at: {time.time():.0f}",
                "---",
                "",
                why.strip()[:MAX_BODY_CHARS],
                "",
            ]
        )
        store.atomic_write(path, rendered, mode=0o644)
        store.write_json(store.tier_b(ctx, f"attempt-{attempt_id}.stamp"), stamped)
    return _parse(path)


def relevant(ctx: GitContext, paths: list[str], limit: int = MAX_SHOWN) -> list[Attempt]:
    """Attempts that touched the files this session is touching.

    By subject rather than by recency: a dead end in the billing code is worth its
    tokens to a session editing billing and is pure noise to a session editing the
    landing page.
    """
    if not paths:
        return []
    wanted = set(paths)
    hits = [a for a in load_all(ctx) if set(a.paths) & wanted]
    hits.sort(key=lambda a: a.recorded_at, reverse=True)
    return hits[:limit]


def render_for_board(ctx: GitContext, paths: list[str]) -> str:
    """Warn before the wheel is reinvented, and only then."""
    hits = relevant(ctx, paths)
    if not hits:
        return ""
    lines = ["ALREADY TRIED on these files — do not repeat without reading why:"]
    lines += [f"  - {a.line()}" for a in hits]
    return "\n".join(lines)


def summary(ctx: GitContext) -> dict[str, int]:
    counts = {outcome: 0 for outcome in OUTCOMES}
    for attempt in load_all(ctx):
        counts[attempt.outcome] = counts.get(attempt.outcome, 0) + 1
    return counts
