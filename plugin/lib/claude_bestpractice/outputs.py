"""The digest the model reads in place of a long successful result, and the whole kept beside it.

`digest` decides what of an output a reader acts on. This decides when that is asked — a test
suite, build or install that succeeded, or any output Claude Code would have cut to its first
2 KB anyway — keeps the whole output where the session can Read and Grep it, and writes the
header that says where (decision 0048). The plugin's hooks module asks it through
`claude-bp-mod digest`, after the command ran and before the model sees what it printed.
"""

from __future__ import annotations

import hashlib
import shlex
import time
from pathlib import Path
from typing import Any

from . import config, digest, sessions, store
from .gitctx import GitContext

OUT_DIR = "out"
# How many whole outputs a tree keeps, newest first, and for how long. A session reads the one
# it was just shown; a week is for the founder asking what a run printed.
KEPT = 40
KEPT_SECONDS = 7 * 24 * 3600
# The most of a file Claude Code persisted that is read back to digest. Past it the digest is of
# the first part, and the header still names the whole.
READ_BYTES = 64 * 1024 * 1024


def answer(ctx: GitContext | None, request: dict[str, Any]) -> dict[str, str]:
    """What the model reads in place of one successful Bash result, or {} for the result as it was.

    `request` is what the hooks module sends: the `command` and the `session` it ran in, what
    it printed as `stdout` and `stderr`, and `persisted`, the file Claude Code wrote the whole
    output to when it was too long to show.
    """
    persisted = str(request.get("persisted") or "")
    text = _whole(request, persisted)
    kind = _worth_a_digest(ctx, str(request.get("command") or ""), text, persisted)
    body = digest.condense(text, kind, _touched(ctx, str(request.get("session") or ""))) if kind else None
    where = (persisted or keep(ctx, text)) if body is not None else ""
    if not where:
        return {}
    return {"stdout": f"{header(text, body, where)}\n{body}"}


def _worth_a_digest(ctx: GitContext | None, command: str, text: str, persisted: str) -> str:
    """The kind of output `text` is when a digest of it is wanted, "" when it is to go as it is.

    A suite, a build or an install; anything else only where Claude Code would have shown its
    first 2 KB and a path anyway; never what a session asked to read.
    """
    cfg = config.load(ctx) if ctx is not None else config.Config()
    if len(text) < digest.MIN_CHARS or not cfg.compress_output:
        return ""
    kind = digest.classify(command, _declared(cfg))
    if kind == digest.READ or (kind == digest.OTHER and not persisted):
        return ""
    return kind


def header(text: str, body: str, where: str) -> str:
    """The first line of a digest: what it stands for and where the whole of it is."""
    lines = text.count("\n") + 1
    return (f"[claude-bestpractice digest: {lines:,} lines, {len(text):,} characters cut to the "
            f"{len(body):,} below; the whole output is in {where} — Read or Grep it for what is "
            "not shown]")


def keep(ctx: GitContext | None, text: str) -> str:
    """Write `text` where the session can read it, out of git, and return its path. "" when it
    could not be written, or outside a repository, where there is nowhere of the plugin's."""
    from . import worktree

    if ctx is None:
        return ""
    home = store.tier_a(ctx, OUT_DIR)
    stamp = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    path = home / f"{time.strftime('%Y%m%d-%H%M%S')}-{stamp}.log"
    worktree.hide(ctx)
    try:
        store.ensure_dir(home)
        store.atomic_write(path, text, mode=0o600)
    except OSError:
        return ""
    _prune(home)
    return str(path)


def _prune(home: Path) -> None:
    """Keep the newest `KEPT` outputs of the last `KEPT_SECONDS`, and none older."""
    try:
        kept = sorted(home.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return
    now = time.time()
    for index, path in enumerate(kept):
        try:
            if index >= KEPT or now - path.stat().st_mtime > KEPT_SECONDS:
                path.unlink()
        except OSError:
            continue


def _whole(request: dict[str, Any], persisted: str) -> str:
    """What the command printed: the file Claude Code kept when it holds more than the request."""
    printed = "\n".join(part for part in (str(request.get("stdout") or ""),
                                          str(request.get("stderr") or "")) if part)
    if not persisted:
        return printed
    try:
        with open(persisted, encoding="utf-8", errors="replace") as handle:
            kept = handle.read(READ_BYTES)
    except OSError:
        return printed
    return kept if len(kept) > len(printed) else printed


def _declared(cfg: config.Config) -> tuple[tuple[str, ...], ...]:
    """The test commands the founder declared, as argv: a run of one of them is a suite's."""
    commands = [cfg.test_command, *cfg.test_commands.values()]
    found: list[tuple[str, ...]] = []
    for command in commands:
        argv = _argv(command)
        if argv:
            found.append(argv)
    return tuple(found)


def _argv(command: Any) -> tuple[str, ...]:
    """A declared command as argv, written as a list or as one line."""
    if isinstance(command, list):
        return tuple(str(part) for part in command)
    if isinstance(command, str):
        try:
            return tuple(shlex.split(command))
        except ValueError:
            return ()
    return ()


def _touched(ctx: GitContext | None, session_id: str) -> tuple[str, ...]:
    """The files this session changed, so a line naming one survives the digest."""
    rec = sessions.get(ctx, session_id) if ctx is not None and session_id else None
    return tuple(rec.last_touched) if rec is not None else ()
