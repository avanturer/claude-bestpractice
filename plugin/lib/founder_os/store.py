"""Storage substrate: two tiers, atomic writes, and locks that actually lock.

TIER A  <repo>/.claude/founder-os/   committed, one file per artifact, lifecycle in
                                     the directory name so a transition is `git mv`.
                                     Collision-free filenames mean N sessions produce
                                     N distinct adds and never a merge conflict.

TIER B  <git-common-dir>/founder-os/ ephemeral coordination. The only location that
                                     is shared by every worktree of one clone, is
                                     invisible to git, survives branch switches, and
                                     dies with the clone.

Tier B is entirely derivable from Tier A plus transcripts. `reindex` rebuilds it, and
that path is tested rather than assumed.
"""

from __future__ import annotations

import errno
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .gitctx import GitContext

TIER_A_DIRNAME = ".claude/founder-os"
TIER_B_DIRNAME = "founder-os"

# A lock older than this is presumed abandoned. Long enough that a slow but live
# holder is never robbed; short enough that a crashed session does not wedge the
# repository until someone notices.
LOCK_STALE_SECONDS = 120.0
LOCK_ACQUIRE_TIMEOUT = 10.0
LOCK_POLL_INTERVAL = 0.02


class LockTimeout(RuntimeError):
    """Raised when a lock could not be acquired within the timeout."""


def tier_a(ctx: GitContext, *parts: str) -> Path:
    return ctx.worktree_root.joinpath(TIER_A_DIRNAME, *parts)


def tier_b(ctx: GitContext, *parts: str) -> Path:
    return ctx.common_dir.joinpath(TIER_B_DIRNAME, *parts)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    """Write via temp-in-same-dir, fsync, rename.

    Same directory matters: `os.replace` is only atomic within a filesystem, and a
    temp file in /tmp may be on a different one. The fsync is what makes the content
    durable before the rename makes it visible — without it a crash can leave a
    correctly-named file with zero bytes.
    """
    ensure_dir(path.parent)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        try:
            os.write(fd, data.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """Tolerate a torn or absent file by returning `default`.

    A single corrupt record must never make the whole store unreadable — that failure
    mode is why one widely-used memory server can be bricked by one bad line.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", mode)


def append_jsonl(path: Path, obj: Any, mode: int = 0o600) -> None:
    """Append one record. Append-only files never need a lock between writers.

    O_APPEND writes under the pipe-buffer size are atomic on POSIX, so concurrent
    sessions interleave records without tearing one.
    """
    ensure_dir(path.parent)
    line = json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, mode)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[Any]:
    """Read an append-only log, skipping records damaged by a partial write."""
    out: list[Any] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _lock_is_stale(lock_path: Path, stale_after: float) -> bool:
    """Staleness is judged by the lock file's mtime, never by a timestamp inside it.

    A clock value written into the payload is the holder's opinion; the mtime is the
    filesystem's. Under a clock change the payload lies and the mtime does not.
    """
    try:
        return (time.time() - lock_path.stat().st_mtime) > stale_after
    except FileNotFoundError:
        return False


def _reclaim(lock_path: Path) -> None:
    """Steal a stale lock without ever deleting a live one.

    Rename to a unique name first. If two processes both decide the lock is stale,
    exactly one rename succeeds; the loser's rename fails and it retries against the
    winner's fresh lock. A bare `unlink` here would let the loser delete the lock the
    winner just created.
    """
    victim = lock_path.with_name(f"{lock_path.name}.stale.{uuid.uuid4().hex[:8]}")
    try:
        os.rename(str(lock_path), str(victim))
    except OSError:
        return
    victim.unlink(missing_ok=True)


@contextmanager
def file_lock(
    lock_path: Path,
    timeout: float = LOCK_ACQUIRE_TIMEOUT,
    stale_after: float = LOCK_STALE_SECONDS,
) -> Iterator[None]:
    """Cross-process advisory lock built from O_EXCL. No third-party dependency.

    Callers MUST re-read the guarded file inside the lock. Reading before acquiring
    and writing after is a lost-update race that looks like it works until two
    sessions run at once.
    """
    ensure_dir(lock_path.parent)
    deadline = time.monotonic() + timeout
    payload = json.dumps({"pid": os.getpid(), "acquired_at": time.time()}).encode("utf-8")

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if _lock_is_stale(lock_path, stale_after):
                _reclaim(lock_path)
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(f"could not acquire {lock_path} within {timeout}s")
            time.sleep(LOCK_POLL_INTERVAL)

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


@contextmanager
def guarded_json(
    path: Path, default: Any = None, timeout: float = LOCK_ACQUIRE_TIMEOUT
) -> Iterator[list[Any]]:
    """Read-modify-write a JSON file under a lock, re-reading inside it.

    Yields a one-element list so the caller can replace the value:

        with guarded_json(p, default={}) as box:
            box[0]["k"] = "v"
    """
    with file_lock(path.with_suffix(path.suffix + ".lock"), timeout=timeout):
        box = [read_json(path, default)]
        yield box
        write_json(path, box[0])


def purge_tier_b(ctx: GitContext) -> None:
    """Drop all derived coordination state. Tier B must always be rebuildable."""
    import shutil

    root = tier_b(ctx)
    if root.exists():
        shutil.rmtree(root)
