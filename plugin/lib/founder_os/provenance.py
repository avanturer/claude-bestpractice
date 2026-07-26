"""Link a persisted claim to the code it describes, and invalidate it when that moves.

This is the mechanism no surveyed tool ships. The industry state of the art is one
mtime comparison, coarse enough that reformatting a file discards every prior insight
about it. One tool captures the git SHA on every checkpoint and never reads it back.
One has real semantic invalidation but only fires it when a new episode happens to
surface the stale fact — stop discussing a topic and its facts stay valid forever.

The mechanism here is deliberately mechanical: every claim records the paths it was
derived from and the git blob hash of each at the time. A later sweep re-hashes those
paths; anything that changed marks the claim SUSPECT, which suppresses it from
injection and surfaces a count.

Blob hashes, never mtimes. Creating a worktree or checking out a branch resets every
mtime in the tree, which would invalidate the whole store at once — and it is exactly
what a several-sessions-at-once workflow does all day. A blob hash is content, so it
survives all of that and still catches a one-character edit.

SUSPECT, not deleted. A claim whose subject moved is usually still mostly right, and a
system that silently drops knowledge is one nobody can debug. It stops being asserted;
it does not stop existing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .gitctx import GitContext

FRESH = "fresh"
SUSPECT = "suspect"
GONE = "gone"


@dataclass(frozen=True)
class Stamp:
    path: str
    blob: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "blob": self.blob}

    @classmethod
    def from_dict(cls, raw: dict) -> "Stamp | None":
        path, blob = raw.get("path"), raw.get("blob")
        return cls(str(path), str(blob)) if path and blob else None


def hash_paths(ctx: GitContext, relpaths: list[str]) -> dict[str, str]:
    """Blob hash per path, in one git invocation.

    `git hash-object` hashes working-tree content, so an uncommitted edit is visible.
    Missing files are reported by absence rather than by an exception: a deleted
    subject is drift, not an error.
    """
    existing = [rel for rel in relpaths if (ctx.worktree_root / rel).is_file()]
    if not existing:
        return {}
    proc = subprocess.run(
        ["git", "hash-object", "--", *existing],
        cwd=str(ctx.worktree_root),
        capture_output=True,
        encoding="utf-8", errors="surrogateescape",
        timeout=60,
    )
    if proc.returncode != 0:
        return {}
    hashes = proc.stdout.split()
    return dict(zip(existing, hashes)) if len(hashes) == len(existing) else {}


def stamp(ctx: GitContext, relpaths: list[str]) -> list[dict[str, str]]:
    """Record what a claim was derived from, so drift is detectable later."""
    hashes = hash_paths(ctx, sorted(set(relpaths)))
    return [Stamp(path, blob).to_dict() for path, blob in sorted(hashes.items())]


def check(ctx: GitContext, stamps: list[dict]) -> tuple[str, list[str]]:
    """Classify a claim against its recorded subjects.

    Returns (status, changed_paths). An unstamped claim is FRESH rather than SUSPECT:
    absence of provenance is our failure to record it, and punishing the claim would
    hide older knowledge for a reason the founder never caused.
    """
    parsed = [s for s in (Stamp.from_dict(raw) for raw in stamps or []) if s]
    if not parsed:
        return FRESH, []

    current = hash_paths(ctx, [s.path for s in parsed])
    missing = [s.path for s in parsed if s.path not in current]
    if missing and len(missing) == len(parsed):
        return GONE, missing

    changed = [s.path for s in parsed if current.get(s.path) not in (None, s.blob)]
    changed += missing
    return (SUSPECT, sorted(set(changed))) if changed else (FRESH, [])


def annotate(ctx: GitContext, claims: list[dict], key: str = "subject_paths") -> list[dict]:
    """Attach a provenance status to each claim, in place of dropping any.

    Callers decide what to do with it. The board suppresses SUSPECT and GONE from the
    injected text and reports the count, so the founder can see that knowledge exists
    and needs repair rather than wondering where it went.
    """
    out: list[dict] = []
    for claim in claims:
        status, changed = check(ctx, claim.get(key) or [])
        enriched = dict(claim)
        enriched["provenance"] = status
        if changed:
            enriched["provenance_changed"] = changed
        out.append(enriched)
    return out


def summarize(claims: list[dict]) -> dict[str, int]:
    counts = {FRESH: 0, SUSPECT: 0, GONE: 0}
    for claim in claims:
        status = claim.get("provenance", FRESH)
        counts[status] = counts.get(status, 0) + 1
    return counts
