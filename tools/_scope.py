#!/usr/bin/env python3
"""Which subtrees inside this repository are not this repository's code to check.

Asked of `git worktree list`, never of a directory name. The plugin provisions worktrees
under `.claude/worktrees/` by default and the founder may put them anywhere, so the name is
a convention while the answer is a fact git already holds.

Without this every checker that walks the tree descended into a provisioned worktree and
reported a full second copy of the repository as the founder's own code. Measured on this
repository with one empty worktree present: 22,044 duplicate blocks against a budget of
zero, plus 38 complex functions against 19 — and `make check`, the one definition of done,
exited 1. For anyone using the plugin's default worktree flow that is every run.
"""

from __future__ import annotations

import pathlib
import subprocess


def nested_worktrees(root: pathlib.Path) -> list[pathlib.Path]:
    """Worktree roots that live inside `root` — never `root` itself.

    Returns nothing when git cannot answer, which leaves the caller scanning everything:
    the pre-existing behaviour, and the safe direction for a checker.
    """
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    here = root.resolve()
    found = []
    for line in proc.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        path = pathlib.Path(line[len("worktree "):].strip()).resolve()
        if path != here and here in path.parents:
            found.append(path)
    return found


def is_inside(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)
