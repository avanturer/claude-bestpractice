"""Session registry and file leases — the cross-session visibility nobody ships.

One file per session, never a shared mutable index. That is the whole concurrency
story: N sessions produce N distinct writes and never contend. The only shared
mutable structure is the lease table, and it is guarded.

Liveness needs POSITIVE evidence of death, never mere silence. An earlier version
declared a session dead once its heartbeat went quiet for fifteen minutes, and that
was the single worst defect this plugin has had: a founder who thinks for a quarter of
an hour comes back to a session whose record a sibling has deleted, after which every
gate takes the missing-record branch and enforces nothing for the rest of its life.
Secrets written, untested work accepted, and no signal anywhere. Silence is what a
working session looks like while a human reads.

So death is: the process is gone, or the pid was recycled by a different process, or
the worktree is no longer registered with git. A quiet heartbeat is grounds for death
only past a ceiling far longer than any think, and only as a backstop against records
that outlived a reboot.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from . import store
from .gitctx import GitContext, worktree_paths

# Older than this and the board stops calling the session active. It is a DISPLAY
# threshold: a quiet session is still enforcing, and reaping on it is what broke the
# gates. Death is decided by the process, not by the clock.
HEARTBEAT_STALE_SECONDS = 900.0

# The backstop for a record that outlived the process it describes — a hard reboot
# reuses pids from 1 and can hand a stale record a live, unrelated pid. Long enough
# that no amount of thinking, lunch, or an overnight pause reaches it.
HEARTBEAT_DEAD_SECONDS = 36 * 3600.0

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
    pid_fingerprint: str = ""
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
    # NEVER os.kill on Windows. There, signal 0 is CTRL_C_EVENT and every other signal
    # goes through TerminateProcess — so the "is this process alive?" probe INTERRUPTS OR
    # KILLS the process it is asking about. On a founder's machine that is a liveness
    # check that reaches across and takes down a sibling Claude Code session.
    #
    # "Cannot tell" resolves to ALIVE, which is this module's rule everywhere else: the
    # cost of a wrong reap is a disarmed session, and the heartbeat ceiling is the backstop
    # for a record that outlived its process.
    if os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user: it exists, which is all we asked.
        return True
    return True


def pid_fingerprint(pid: int) -> str:
    """Tell a still-running process apart from a stranger wearing its pid.

    Boot-relative start time, which no two processes on one boot can share for the same
    pid. Empty where the kernel does not expose it, and an empty fingerprint is never
    treated as a mismatch — an unsupported platform must not start reaping live work.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            # The comm field can itself contain ')', so split from the right.
            fields = handle.read().rsplit(b")", 1)[1].split()
        return fields[19].decode("ascii")
    except (OSError, IndexError, UnicodeDecodeError):
        return ""


def is_live(ctx: GitContext, rec: SessionRecord, known_worktrees: set[str] | None = None) -> bool:
    if not pid_alive(rec.pid):
        return False
    # Only a POSITIVE mismatch kills. Both sides empty means we cannot tell, and "cannot
    # tell" must resolve to live, because the cost of a wrong reap is a disarmed session.
    current = pid_fingerprint(rec.pid)
    if rec.pid_fingerprint and current and current != rec.pid_fingerprint:
        return False
    if (time.time() - rec.heartbeat_at) > HEARTBEAT_DEAD_SECONDS:
        return False
    if known_worktrees is None:
        known_worktrees = {p.as_posix() for p in worktree_paths(ctx)}
    # An empty set means `git worktree list` failed; do not reap on a failed probe.
    if known_worktrees and rec.worktree not in known_worktrees:
        return False
    return True


def is_idle(rec: SessionRecord) -> bool:
    """Quiet for a while. Cosmetic — the board dims it; nothing is enforced differently."""
    return (time.time() - rec.heartbeat_at) > HEARTBEAT_STALE_SECONDS


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
    # Always re-stamp: a resume rewrites `pid` to the new CLI process, and keeping the
    # previous process's start time made is_live read its own record as a recycled pid —
    # positive evidence of death for a session that had just started.
    rec.pid_fingerprint = pid_fingerprint(rec.pid) or rec.pid_fingerprint
    store.write_json(_record_path(ctx, rec.session_id), rec.to_dict())


def branch_point(ctx: GitContext) -> str:
    """Where this branch diverged from the trunk, or HEAD when there is no trunk.

    Used only when the real baseline is unrecoverable. Everything since the branch point
    is treated as this session's work, which is deliberately too much: an over-wide diff
    asks for evidence that was already earned, while a too-narrow one lets unverified
    work through silently.
    """
    from .gitctx import _run

    for trunk in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        base = _run(["merge-base", "HEAD", trunk], ctx.worktree_root, check=False)
        if base and base != ctx.head:
            return base
    return ctx.head


def adopt(ctx: GitContext, session_id: str) -> SessionRecord:
    """Re-register a session whose record is missing, and keep enforcing.

    A gate that finds no record has two options and only one of them is defensible.
    Allowing means a deleted file silently switches off the secret scan, the leases and
    the evidence gate — enforcement you can remove with `rm`.

    The baseline is where this gets subtle. Anchoring at HEAD looks natural and is
    exactly wrong: every commit the session already made then falls outside the diff, the
    evidence gate is handed an empty change list, and it allows the turn over a red
    suite. So the fallback is the branch point, which over-reports rather than
    under-reports — and for a fail-closed gate, demanding evidence for slightly too much
    is the survivable direction.
    """
    rec = SessionRecord(
        session_id=session_id or f"anon-{owning_pid()}",
        pid=owning_pid(),
        worktree=ctx.worktree_root.as_posix(),
        branch=ctx.branch,
        baseline_commit=branch_point(ctx),
        started_at=time.time(),
        heartbeat_at=time.time(),
    )
    register(ctx, rec)
    return rec


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


def reap(ctx: GitContext, exclude: str | None = None) -> list[SessionRecord]:
    """Remove dead sessions and release their leases. Returns what was reaped.

    Without this, every tool in the surveyed field leaves a crashed session marked
    in-progress forever, and its file leases poison those paths permanently.
    """
    known = {p.as_posix() for p in worktree_paths(ctx)}
    dead: list[SessionRecord] = []
    for rec in load_all(ctx):
        # The session firing SessionStart right now is alive by definition, and
        # reaping it here destroyed its baseline: the rebuild found no record and
        # re-derived from HEAD, so every commit already made fell outside the diff
        # and the Stop gate saw nothing to verify. An ordinary resume disarmed it.
        if rec.session_id == exclude:
            continue
        if not is_live(ctx, rec, known):
            dead.append(rec)
            _record_path(ctx, rec.session_id).unlink(missing_ok=True)
            # The baseline goes into the reap log, not just the record. A crashed
            # session is reaped by a sibling, and when the founder resumes it the rebuild
            # finds no record and re-anchors at HEAD — so every commit made before the
            # crash falls outside the diff and the Stop gate has nothing to verify.
            # Reaping the process must not amnesty the work it already did.
            store.append_jsonl(
                store.tier_b(ctx, REAPED_LOG),
                {
                    "session_id": rec.session_id,
                    "pid": rec.pid,
                    "reaped_at": time.time(),
                    "baseline_commit": rec.baseline_commit,
                    "task_statement": rec.task_statement,
                    "task_paths": rec.task_paths,
                },
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


def reaped_memory(ctx: GitContext, session_id: str) -> dict:
    """What a reaped session knew, so resuming it does not start from a clean slate."""
    latest: dict = {}
    for entry in store.read_jsonl(store.tier_b(ctx, REAPED_LOG)):
        if isinstance(entry, dict) and entry.get("session_id") == session_id:
            latest = entry
    return latest


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


def _lease_table(box_value: object) -> dict[str, dict]:
    """The lease table with every unusable row dropped, never a crash.

    The outer `isinstance(..., dict)` check guarded the table and not its rows, so one
    corrupt value — a truncated write, a hand-edit, a `>` into the file — made every
    `holder.get(...)` raise AttributeError inside the pre-write gate. That gate is
    fail-closed, so a single malformed byte in an ephemeral cache file would have
    refused every write in every session on the clone until someone found the file.

    A row that cannot be read is treated as no row: the path is simply unclaimed, and
    the next acquire overwrites it. Losing one lease is recoverable. Losing the ability
    to write is not.
    """
    if not isinstance(box_value, dict):
        return {}
    return {
        path: holder
        for path, holder in box_value.items()
        if isinstance(path, str) and isinstance(holder, dict)
    }


def acquire_lease(ctx: GitContext, session_id: str, relpath: str, ttl: float = 1800.0) -> str | None:
    """Claim exclusive intent to edit a path.

    Returns None on success, or the owning session id on refusal. Re-acquiring your
    own lease is idempotent, and a lease whose owner is dead or whose TTL expired is
    taken over rather than respected.
    """
    now = time.time()
    with store.guarded_json(_leases_path(ctx), default={}) as box:
        table = _lease_table(box[0])
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
        table = _lease_table(box[0])
        for path in [p for p, h in table.items() if h.get("session_id") in session_ids]:
            table.pop(path, None)
            removed += 1
        box[0] = table
    return removed


def leases_held_by(ctx: GitContext, session_id: str) -> list[str]:
    table = _lease_table(store.read_json(_leases_path(ctx), default={}))
    return sorted(p for p, h in table.items() if h.get("session_id") == session_id)
