"""A numbered migration a branch adds, whose number the trunk has already given to another file.

Parallel sessions each take "the next number" from the trunk they started from, and both are
right until the second merges: a founder's clone held ten numbers twice over (0086, 0089, 0099,
0105 to 0109, 0115, 0148). A runner that keys on the file name applies both, in an order no one
chose; one that keys on the number, or Django's, refuses the second. Asked at merge time, of the
files the pull request adds, because that is when the clash exists and renaming is one command.
"""

from __future__ import annotations

import re
import subprocess

from .gitctx import GitContext

# A migration by its place and its name: a file under a `migrations`, `migrate` or `migration`
# directory whose name starts with its number, as `0188_feedback.sql`, `0042_add_index.py`,
# `V12__init.sql` or `000012_users.up.sql` do. A timestamp is a number nobody else draws.
_MIGRATION = re.compile(
    r"(?:^|/)(?:migrations?|migrate)/(?:[^/]+/)*?(?P<number>V?\d{1,6})(?:__|_)[^/]+$")


def clashes(ctx: GitContext, base: str, head: str) -> list[str]:
    """One line per migration `head` adds over `base` whose number `base` holds in another file.

    Empty where git cannot answer: a check that refuses on its own failure refuses every merge
    of a repository it cannot read.
    """
    trunk = _ref(ctx, base)
    added = _lines(ctx, ["diff", "--name-only", "--diff-filter=A", "--no-renames",
                         f"{trunk}...{head}"]) if trunk else None
    out: list[str] = []
    for path in added or []:
        found = _MIGRATION.search(path)
        if not found:
            continue
        folder = path.rsplit("/", 1)[0]
        taken = [name for name in _lines(ctx, ["ls-tree", "--name-only", f"{trunk}:{folder}"]) or []
                 if _number_of(name) == found["number"] and f"{folder}/{name}" != path]
        if taken:
            out.append(f"the migration {path} takes number {found['number']}, which {base} already "
                       f"gives {taken[0]}: rename it to {_next_free(ctx, trunk, head, folder, found)}")
    return out


def _number_of(name: str) -> str:
    found = _MIGRATION.search(f"migrations/{name}")
    return found["number"] if found else ""


def _next_free(ctx: GitContext, trunk: str, head: str, folder: str, found: re.Match) -> str:
    """The number after every one the trunk and this branch use in `folder`, written as wide."""
    used = [_number_of(name) for ref in (trunk, head)
            for name in _lines(ctx, ["ls-tree", "--name-only", f"{ref}:{folder}"]) or []]
    digits = [int(number.lstrip("V")) for number in used if number]
    prefix = "V" if found["number"].startswith("V") else ""
    width = len(found["number"].lstrip("V"))
    return f"{prefix}{max(digits, default=0) + 1:0{width}d}"


def _ref(ctx: GitContext, base: str) -> str:
    """`origin/<base>`, or `<base>` in a clone without a remote copy of it. "" when neither is."""
    for ref in (f"origin/{base}", base):
        if _lines(ctx, ["rev-parse", "--verify", "--quiet", ref]):
            return ref
    return ""


def _lines(ctx: GitContext, args: list[str]) -> list[str] | None:
    """What one git command prints, a line each; None when it fails."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(ctx.worktree_root), capture_output=True,
                              encoding="utf-8", errors="surrogateescape", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]
