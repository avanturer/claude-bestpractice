"""Git working rules, enforced rather than requested.

Two rules, both chosen because their violation is silent and expensive under several
sessions at once:

**Work happens in a worktree, never in the main checkout.** Worktrees are what make
parallel sessions possible at all — separate files, separate branch, one shared object
store. A session that edits the main checkout is sharing a working tree with every
other session that does the same, and the symptom is not a merge conflict. It is one
session's edit vanishing under another's, with neither told. Git has no mechanism that
notices.

**The trunk is not edited directly.** Not because a solo founder needs review, but
because a branch is what makes work revertible as a unit and mergeable as a unit. Commit
straight onto main and the only way to undo a bad afternoon is to pick commits apart.

Both refuse with the exact command that fixes them. A rule that only says no teaches the
agent to route around it; a rule that says "run this instead" gets followed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .gitctx import GitContext

# Names git itself treats as the trunk. Checked against the actual default when the
# repository has a remote, and against this list when it does not.
TRUNK_NAMES = ("main", "master", "trunk", "develop", "development")


def default_branch(ctx: GitContext) -> str:
    """What this repository considers its trunk, asked rather than assumed.

    A repository whose default branch is not called `main` is common enough — and this
    plugin's own repository is one — that guessing would refuse work on a perfectly
    ordinary branch.
    """
    probe = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=str(ctx.worktree_root), capture_output=True, text=True, timeout=30,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip().split("/", 1)[-1]

    listed = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(ctx.worktree_root), capture_output=True, text=True, timeout=30,
    ).stdout.split()
    for name in TRUNK_NAMES:
        if name in listed:
            return name
    return ""


def on_trunk(ctx: GitContext) -> bool:
    trunk = default_branch(ctx)
    return bool(trunk) and ctx.branch == trunk


def has_history(ctx: GitContext) -> bool:
    """A repository with no commits yet is being born; nothing to protect."""
    return bool(ctx.head)


def worktree_advice(ctx: GitContext, task: str = "") -> str:
    """The exact command that gets this session into its own worktree."""
    slug = "-".join(
        part for part in "".join(c.lower() if c.isalnum() else " " for c in task).split()[:5]
    ) or "work"
    target = ctx.worktree_root.parent / f"{ctx.worktree_root.name}-{slug}"
    return f"git worktree add -b feat/{slug} {target}"


def violations(ctx: GitContext, task: str = "") -> list[str]:
    """Refusals for this write, each with the command that resolves it.

    Empty on a repository with no commits: the first session in a fresh project has
    nowhere to branch from, and refusing it would make the plugin impossible to adopt.
    """
    if not has_history(ctx):
        return []

    out: list[str] = []
    if not ctx.is_worktree:
        out.append(
            "founder-os: this is the main checkout, not a worktree. Several sessions "
            "sharing one working tree overwrite each other silently — git does not "
            "notice, and neither will you.\n"
            f"  {worktree_advice(ctx, task)}\n"
            "then work there. Set `require_worktree: false` in "
            ".claude/founder-os/config.json if this repository is genuinely single-session."
        )
    if on_trunk(ctx):
        out.append(
            f"founder-os: {ctx.branch} is the trunk. Work on a branch so it can be "
            "reverted and merged as one unit.\n"
            f"  git switch -c feat/{'-'.join(task.lower().split()[:4]) or 'work'}\n"
            "Set `protect_trunk: false` in .claude/founder-os/config.json to allow it."
        )
    return out


def worktree_paths_in_use(ctx: GitContext) -> dict[str, str]:
    """branch -> worktree path, so the board can name where each session is."""
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(ctx.worktree_root), capture_output=True, text=True, timeout=30,
    )
    out: dict[str, str] = {}
    path = ""
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line.startswith("branch ") and path:
            out[line.split("/")[-1]] = path
    return out
