"""Storage substrate: two tiers, atomic writes, and locks that actually lock.

TIER A  <repo>/.claude/claude-bestpractice/   committed, one file per artifact, lifecycle in
                                     the directory name so a transition is `git mv`.
                                     Collision-free filenames mean N sessions produce
                                     N distinct adds and never a merge conflict.

TIER B  <git-common-dir>/claude-bestpractice/ ephemeral coordination. The only location that
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
import platform
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .gitctx import GitContext

TIER_A_DIRNAME = ".claude/claude-bestpractice"
TIER_B_DIRNAME = "claude-bestpractice"

# The mtime backstop, for a holder whose liveness cannot be established: a lock from a
# different machine over a shared filesystem, or one whose payload never landed. Long
# enough that a slow but live holder is never robbed. A crashed holder on THIS machine
# does not wait for it — see `_lock_is_stale`.
LOCK_STALE_SECONDS = 120.0
LOCK_ACQUIRE_TIMEOUT = 10.0
LOCK_POLL_INTERVAL = 0.02


class LockTimeout(RuntimeError):
    """Raised when a lock could not be acquired within the timeout."""


def tier_a(ctx: GitContext, *parts: str) -> Path:
    return ctx.worktree_root.joinpath(TIER_A_DIRNAME, *parts)


# A path that will never exist and can never be tracked. `git check-ignore` answers about a
# path whether or not it is on disk, but it reports NOTHING for a path already in the index,
# because a tracked file is not subject to exclude rules. Probing the directory itself, or
# any real task file, therefore answers "visible" the moment one file inside has been
# committed — a false all-clear in exactly the case that matters most, a repository that was
# healthy once and has been hidden since. Checked against git rather than reasoned about.
_VISIBILITY_PROBE = ("plan", "next", ".visibility-probe")


def newest_checkpoint(ctx: GitContext, session_id: str) -> str:
    """The last thing written before this session's context was compacted, or "".

    The checkpoint was written on every compaction and never read back — the exact pattern
    `provenance` opens by naming as the way memory features fail: capture something on
    every checkpoint and never look at it again. Compaction is the largest destroyer of
    in-context state, so the half that matters is the restore.
    """
    directory = tier_a(ctx, "checkpoints")
    try:
        found = sorted(directory.glob("*.md"))
    except OSError:
        return ""
    mine = [p for p in found if session_id and session_id[:8] in p.name] or found
    if not mine:
        return ""
    try:
        return mine[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def hidden_from_git(ctx: GitContext) -> str:
    """Where Tier A is being hidden from git, verbatim, or "" when git can see it.

    Tier A is committed by definition (decision 0001), and the whole promise of parking a
    task is that it outlives the session that wrote it. An ignore rule over this directory
    voids that promise in silence: `park` prints an id, `list` shows the task, `adopt
    --check` reports zero left, and only `git status` disagrees. Reported as issue #66 by a
    repository where thirty migrated tasks turned out to be invisible to git.

    This only ever REPORTS. The one ignore rule this plugin writes is `worktree.hide`,
    which excludes the trees it provisions and nothing else — so a rule over Tier A belongs
    to the founder or to another tool, and rewriting somebody else's ignore file on the
    strength of a guess about why it is there is not a repair.
    """
    from .gitctx import _run

    relative = "/".join((TIER_A_DIRNAME, *_VISIBILITY_PROBE))
    try:
        out = _run(["check-ignore", "-v", relative], ctx.worktree_root, check=False)
    except Exception:  # noqa: BLE001 - a diagnostic must never be what breaks a session
        return ""
    # `<source>:<line>:<pattern>\t<path>`. The left half is precisely what somebody needs to
    # go find the rule, so it is returned whole rather than split into pieces nobody reads.
    return out.split("\t", 1)[0].strip() if out else ""


def ignored_tier_a(ctx: GitContext) -> list[str]:
    """Durable records an ignore rule is keeping out of git — the ones that die here.

    `--ignored` with `--exclude-standard` is the combination that LISTS what the rules
    hide; `--others --exclude-standard` alone filters exactly those out and answers with
    the ordinary untracked files instead, which is the opposite question. Checked against
    a repository that had two, not reasoned about.

    Asked of git rather than of the rule text: a file committed BEFORE the rule appeared
    stays tracked and is not lost, and counting it would make the number untrustworthy at
    the moment somebody has to act on it.
    """
    from .gitctx import _run

    try:
        out = _run(
            ["ls-files", "--others", "--ignored", "--exclude-standard", TIER_A_DIRNAME],
            ctx.worktree_root, check=False,
        )
    except Exception:  # noqa: BLE001 - a count is a diagnostic; never fail a session over one
        return []
    return [line for line in (out or "").splitlines() if line.strip()]


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


def dumps(obj: Any, **kwargs: Any) -> str:
    """JSON that can always be encoded, without making readable content unreadable.

    A path git handed us that is not valid UTF-8 arrives as a string carrying lone
    surrogates — that is what makes it still open the real file. `ensure_ascii=False`
    then raises UnicodeEncodeError at `.encode()`, and the raise happens inside a
    fail-closed gate, so one oddly-named file in the repository refused every write in
    the session.

    `ensure_ascii=True` escapes the surrogate to `\\udce9`, which is pure ASCII, encodes
    fine, and reads back as the identical string. But it also escapes every Cyrillic and
    CJK character, and Tier A files are committed and read by humans. So: the readable
    form first, the escaped form only for the payload that cannot take it.
    """
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(obj, ensure_ascii=True, **kwargs)
    return text


def write_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    atomic_write(path, dumps(obj, indent=2, sort_keys=True) + "\n", mode)


def append_jsonl(path: Path, obj: Any, mode: int = 0o600) -> None:
    """Append one record. Append-only files never need a lock between writers.

    O_APPEND writes under the pipe-buffer size are atomic on POSIX, so concurrent
    sessions interleave records without tearing one.
    """
    ensure_dir(path.parent)
    line = dumps(obj, sort_keys=True) + "\n"
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


def lock_identity() -> str:
    """Where a pid written into a lock is the same number this process would see.

    Hostname plus pid-namespace inode. Two containers on one host share a boot and a
    clock but not a pid namespace, so pid 1234 in one is a different process — or no
    process — in the other. Comparing the pair is what makes it safe to ask the kernel
    whether a lock holder is still alive.
    """
    try:
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        namespace = ""
    return f"{platform.node()}/{namespace}"


def _lock_is_stale(lock_path: Path, stale_after: float) -> bool:
    """Dead holder, or an old enough file. Two rules, and the first one is the useful one.

    Time alone was the whole test, and it made a crash cost every other session two
    minutes of hard failure: the lock is not stale until `stale_after`, `file_lock`
    gives up after ten seconds, and a gate that raises is a gate that fails closed. One
    session crashing at the wrong instant refused writes across the entire clone.

    So ask the kernel first. When the payload names this machine's pid namespace, a pid
    that no longer exists is positive evidence of death and the lock is reclaimable now.

    The mtime rule stays as the backstop for everything that cannot be answered that
    way — an unreadable or half-written payload, a holder on another machine over a
    shared filesystem. Staleness there is judged by the file's mtime and never by a
    timestamp inside it: a clock value in the payload is the holder's opinion, the mtime
    is the filesystem's, and under a clock change the payload lies while the mtime does not.
    """
    try:
        stat = lock_path.stat()
    except FileNotFoundError:
        return False

    holder = _read_lock_payload(lock_path)
    if holder.get("identity") == lock_identity():
        pid = holder.get("pid")
        if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
            return True

    return (time.time() - stat.st_mtime) > stale_after


def _read_lock_payload(lock_path: Path) -> dict:
    """The holder's own description, or an empty one. Never raises, never blocks.

    The window between O_EXCL creating the file and the payload being written is real,
    and a reader landing inside it sees zero bytes. That is not an error — it resolves
    to "cannot tell", which falls through to the mtime rule.
    """
    try:
        raw = lock_path.read_bytes()
    except OSError:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pid_alive(pid: int) -> bool:
    """Signal 0 probes existence without delivering anything.

    `PermissionError` means the process exists and belongs to someone else, which is
    all that was asked. Treating it as dead would let one user steal another's lock.
    """
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
    except (PermissionError, OSError):
        return True
    return True


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
    payload = json.dumps(
        {"pid": os.getpid(), "acquired_at": time.time(), "identity": lock_identity()}
    ).encode("utf-8")

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


# Tier B is DESCRIBED as entirely derived, and four of its files are not. These record
# events — a finish that could not be proved, a suite observed failing, a decision the
# agent drafted and nobody has accepted yet — and no amount of rescanning the repository
# brings an event back. Purging them was silent, permanent, and `claude-bp reindex`
# printed "Nothing durable was lost" over the top of it.
#
# Keeping the list here rather than in the command: the destruction lives here, so the
# exception has to as well, or the next caller of `purge_tier_b` repeats the bug.
CARRIED = (
    "open-items.jsonl",       # board.OPEN_ITEMS_FILE — including UNVERIFIED warnings
    "decision-inbox.jsonl",   # drafts.INBOX_FILE — drafted, not yet accepted
    "unverified.jsonl",       # the evidence gate's record of a finish it could not prove
)
# `failing-suite.json` is deliberately absent: the red ledger is Tier A, committed, and
# this function never reaches it.

# Same rule, one directory rather than one file. A note another session queued is an event
# too — the lease conflict that produced it happened at a moment that rescanning cannot
# reconstruct — and it is a directory only because a file per recipient is what keeps eight
# sessions off one lock.
CARRIED_DIRS = (
    "inbox",                  # inbox.DIRNAME — facts queued for a session, not yet read
)


def purge_tier_b(ctx: GitContext) -> None:
    """Drop derived coordination state, keeping the records that cannot be rebuilt.

    Everything else here IS derivable — session records re-register on the next hook, the
    repomap cache rescans, the stage signals re-probe, locks are meaningless once the
    holders are gone. Those are what this is for.
    """
    import shutil

    root = tier_b(ctx)
    if not root.exists():
        return

    kept = {name: (root / name).read_bytes() for name in CARRIED if (root / name).is_file()}
    # Held BESIDE the root, not in the system temp directory: a move within one filesystem
    # is atomic and cannot half-copy, and `/tmp` is frequently a different mount.
    carry = root.parent / f".{root.name}.carry"
    shutil.rmtree(carry, ignore_errors=True)
    for name in CARRIED_DIRS:
        if (root / name).is_dir():
            ensure_dir(carry)
            shutil.move(str(root / name), str(carry / name))

    shutil.rmtree(root)
    ensure_dir(root)
    for name, blob in kept.items():
        atomic_write(root / name, blob.decode("utf-8", "surrogateescape"))
    for name in CARRIED_DIRS:
        if (carry / name).is_dir():
            shutil.move(str(carry / name), str(root / name))
    shutil.rmtree(carry, ignore_errors=True)
