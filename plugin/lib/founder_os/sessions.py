"""Session registry and file leases — the cross-session visibility nobody ships.

One file per session, never a shared mutable index. That is the whole concurrency
story: N sessions produce N distinct writes and never contend. The only shared
mutable structure is the lease table, and it is guarded.

Liveness is two-part: the pid must be alive AND the worktree must still be
registered with git. A pid check alone reports a dead session as live the moment the
OS reuses its pid, and a session whose worktree was removed is dead regardless.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from . import store
from .gitctx import GitContext, worktree_paths

# A session that has not touched its record in this long is presumed dead. Hooks fire
# on every tool call, so a live session refreshes far more often than this.
HEARTBEAT_STALE_SECONDS = 900.0

SESSIONS_DIR = "sessions"


def owning_pid() -> int:
    """The process to watch for liveness: the one that SPAWNED this hook.

    A hook is a short-lived subprocess — it exits milliseconds after it runs, so
    recording its own pid would mark every session dead almost immediately and the
    next session would reap it. The parent is the Claude Code process that owns the
    session, which is the thing whose death actually means the session is over.

    The heartbeat is still the primary signal; this only catches a hard crash before
    the heartbeat goes stale.
    """
    return os.getppid()
LEASES_FILE = "leases.json"
REAPED_LOG = "reaped.jsonl"


@dataclass
class SessionRecord:
    session_id: str
    pid: int
    worktree: str
    branch: str
    baseline_commit: str
    started_at: float
    heartbeat_at: float
    task_statement: str = ""
    task_paths: list[str] = field(default_factory=list)
    model: str = ""
    effort: str = ""
    last_touched: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tool_signatures: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionRecord":
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        known.setdefault("session_id", raw.get("session_id", "unknown"))
        return cls(**{k: v for k, v in known.items() if v is not None})


def _sessions_dir(ctx: GitContext) -> Path:
    return store.ensure_dir(store.tier_b(ctx, SESSIONS_DIR))


def _record_path(ctx: GitContext, session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:120]
    return _sessions_dir(ctx) / f"{safe}.json"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user: it exists, which is all we asked.
        return True
    return True


def is_live(ctx: GitContext, rec: SessionRecord, known_worktrees: set[str] | None = None) -> bool:
    if (time.time() - rec.heartbeat_at) > HEARTBEAT_STALE_SECONDS:
        return False
    if not pid_alive(rec.pid):
        return False
    if known_worktrees is None:
        known_worktrees = {p.as_posix() for p in worktree_paths(ctx)}
    # An empty set means `git worktree list` failed; do not reap on a failed probe.
    if known_worktrees and rec.worktree not in known_worktrees:
        return False
    return True


def load_all(ctx: GitContext) -> list[SessionRecord]:
    out: list[SessionRecord] = []
    for path in sorted(_sessions_dir(ctx).glob("*.json")):
        raw = store.read_json(path)
        if isinstance(raw, dict) and raw.get("session_id"):
            try:
                out.append(SessionRecord.from_dict(raw))
            except TypeError:
                continue
    return out


def register(ctx: GitContext, rec: SessionRecord) -> None:
    rec.heartbeat_at = time.time()
    store.write_json(_record_path(ctx, rec.session_id), rec.to_dict())


def get(ctx: GitContext, session_id: str) -> SessionRecord | None:
    raw = store.read_json(_record_path(ctx, session_id))
    if not isinstance(raw, dict) or not raw.get("session_id"):
        return None
    try:
        return SessionRecord.from_dict(raw)
    except TypeError:
        return None


def touch(ctx: GitContext, session_id: str, **updates: Any) -> SessionRecord | None:
    """Refresh the heartbeat and apply field updates.

    Each session owns its own file, so this needs no lock — the only writer is the
    session itself.
    """
    rec = get(ctx, session_id)
    if rec is None:
        return None
    for key, value in updates.items():
        if hasattr(rec, key):
            setattr(rec, key, value)
    rec.heartbeat_at = time.time()
    store.write_json(_record_path(ctx, session_id), rec.to_dict())
    return rec


def unregister(ctx: GitContext, session_id: str) -> None:
    _record_path(ctx, session_id).unlink(missing_ok=True)
    release_all(ctx, session_id)


def reap(ctx: GitContext) -> list[SessionRecord]:
    """Remove dead sessions and release their leases. Returns what was reaped.

    Without this, every tool in the surveyed field leaves a crashed session marked
    in-progress forever, and its file leases poison those paths permanently.
    """
    known = {p.as_posix() for p in worktree_paths(ctx)}
    dead: list[SessionRecord] = []
    for rec in load_all(ctx):
        if not is_live(ctx, rec, known):
            dead.append(rec)
            _record_path(ctx, rec.session_id).unlink(missing_ok=True)
            store.append_jsonl(
                store.tier_b(ctx, REAPED_LOG),
                {"session_id": rec.session_id, "pid": rec.pid, "reaped_at": time.time()},
            )
    if dead:
        _release_many(ctx, {r.session_id for r in dead})
        # Claims on the work ledger are released too. Without this a crashed session
        # leaves its task marked in-flight forever, which is the state every surveyed
        # tool leaves behind and the reason their boards stop being believed.
        from . import plan

        for record in dead:
            try:
                plan.release(ctx, record.session_id)
            except OSError:
                continue
    return dead


def live_sessions(ctx: GitContext, exclude: str | None = None) -> list[SessionRecord]:
    known = {p.as_posix() for p in worktree_paths(ctx)}
    return [
        r
        for r in load_all(ctx)
        if r.session_id != exclude and is_live(ctx, r, known)
    ]


# --------------------------------------------------------------------------- leases


def _leases_path(ctx: GitContext) -> Path:
    return store.tier_b(ctx, LEASES_FILE)


def acquire_lease(ctx: GitContext, session_id: str, relpath: str, ttl: float = 1800.0) -> str | None:
    """Claim exclusive intent to edit a path.

    Returns None on success, or the owning session id on refusal. Re-acquiring your
    own lease is idempotent, and a lease whose owner is dead or whose TTL expired is
    taken over rather than respected.
    """
    now = time.time()
    with store.guarded_json(_leases_path(ctx), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        holder = table.get(relpath)
        if holder and holder.get("session_id") != session_id:
            expires = holder.get("expires_at", 0)
            if expires > now and pid_alive(int(holder.get("pid", -1))):
                return str(holder.get("session_id"))
        table[relpath] = {
            "session_id": session_id,
            "pid": owning_pid(),
            "acquired_at": now,
            "expires_at": now + ttl,
        }
        box[0] = table
    return None


def release_all(ctx: GitContext, session_id: str) -> int:
    return _release_many(ctx, {session_id})


def _release_many(ctx: GitContext, session_ids: set[str]) -> int:
    removed = 0
    with store.guarded_json(_leases_path(ctx), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        for path in [p for p, h in table.items() if h.get("session_id") in session_ids]:
            table.pop(path, None)
            removed += 1
        box[0] = table
    return removed


def leases_held_by(ctx: GitContext, session_id: str) -> list[str]:
    table = store.read_json(_leases_path(ctx), default={}) or {}
    if not isinstance(table, dict):
        return []
    return sorted(p for p, h in table.items() if h.get("session_id") == session_id)
