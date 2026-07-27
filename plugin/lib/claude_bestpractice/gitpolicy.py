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

Then two more that are cheap and that nobody enforces on themselves: a commit message
has to describe the change rather than the act of committing, and merge conflict markers
must never reach a file.

Every refusal here carries the exact command or edit that fixes it. A rule that only says
no teaches the agent to route around it; a rule that says "do this instead" gets followed.
"""

from __future__ import annotations

import re
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
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip().split("/", 1)[-1]

    listed = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
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
            "claude-bestpractice: this is the main checkout, not a worktree. Several sessions "
            "sharing one working tree overwrite each other silently — git does not "
            "notice, and neither will you.\n"
            f"  {worktree_advice(ctx, task)}\n"
            "then work there. Set `require_worktree: false` in "
            ".claude/claude-bestpractice/config.json if this repository is genuinely single-session."
        )
    if on_trunk(ctx):
        out.append(
            f"claude-bestpractice: {ctx.branch} is the trunk. Work on a branch so it can be "
            "reverted and merged as one unit.\n"
            f"  git switch -c feat/{'-'.join(task.lower().split()[:4]) or 'work'}\n"
            "Set `protect_trunk: false` in .claude/claude-bestpractice/config.json to allow it."
        )
    return out


def worktree_paths_in_use(ctx: GitContext) -> dict[str, str]:
    """branch -> worktree path, so the board can name where each session is."""
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    out: dict[str, str] = {}
    path = ""
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line.startswith("branch ") and path:
            out[line.split("/")[-1]] = path
    return out


# ------------------------------------------------------------------ commit messages

# Messages that describe the act of committing rather than the change. Every one of
# these is what gets typed when the author has stopped thinking about the reader.
_EMPTY_MESSAGES = {
    "wip", "fix", "fixes", "fixed", "update", "updates", "updated", "changes", "change",
    "stuff", "misc", "cleanup", "clean up", "refactor", "tweak", "tweaks", "minor",
    "temp", "test", "asdf", ".", "..", "commit", "small fix", "quick fix", "final",
}

# Conventional Commits, which is the established convention rather than a preference:
# it is machine-readable, drives changelog and version tooling, and its scope names the
# subsystem a future session needs to find.
CONVENTIONAL = re.compile(
    r"^(?:build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(?:\([\w./-]+\))?!?: .{6,}"
)

MIN_SUBJECT_CHARS = 15
MAX_SUBJECT_CHARS = 72

# `-m`, `--message=`, and combined short flags like `-qm` — all of which are ordinary,
# and the last of which a naive `-m` pattern misses entirely, so the check silently
# passes on exactly the commits someone typed in a hurry.
COMMIT_MESSAGE = re.compile(
    r"""git\s+commit\b[^\n]*?(?:-[a-zA-Z]*m|--message=?)\s*(?P<q>["'])(?P<message>.*?)(?P=q)""",
    re.S,
)

# `=======` alone is also how Markdown and reStructuredText underline a seven-character
# heading, so requiring the OPENING marker as well is what stops `Options` under a row of
# equals signs being hard-refused as an unresolved conflict.
CONFLICT_MARKERS = re.compile(r"(?m)^<{7}(?:\s|$).*?^={7}(?:\s|$)", re.S)


def commit_message(command: str) -> str:
    """The message out of a `git commit -m` command line, or empty if there is none."""
    match = COMMIT_MESSAGE.search(command)
    return match.group("message") if match else ""


def message_complaint(message: str, conventional: bool = True) -> str:
    """Why this message fails a reader six months from now. Empty when it is fine.

    The audience is not a reviewer — there is none. It is the next session, which will
    `git log` this file to work out why it looks the way it does. "fix" answers nothing.
    """
    subject = message.strip().splitlines()[0].strip() if message.strip() else ""
    if not subject:
        return "the commit message is empty"
    if subject.lower().rstrip(".!") in _EMPTY_MESSAGES:
        return (
            f"{subject!r} describes committing, not the change. The next session will read "
            "this to work out why the code looks the way it does."
        )
    if len(subject) < MIN_SUBJECT_CHARS:
        return f"{subject!r} is {len(subject)} characters; say what changed and why"
    if len(subject) > MAX_SUBJECT_CHARS and "\n" not in message.strip():
        return (
            f"the subject is {len(subject)} characters. Keep it under {MAX_SUBJECT_CHARS} "
            "and put the detail in a body after a blank line."
        )
    if conventional and not CONVENTIONAL.match(subject):
        return (
            f"{subject!r} is not a conventional commit. Use `type(scope): summary` — "
            "feat, fix, docs, refactor, perf, test, build, ci, chore. It is machine-readable, "
            "drives changelogs and versioning, and its scope names the subsystem."
        )
    return ""


def conflict_complaint(content: str) -> str:
    """Unresolved merge markers, which compile in almost no language and ship in many."""
    if not CONFLICT_MARKERS.search(content):
        return ""
    return (
        "This content still contains merge conflict markers (<<<<<<<, =======, >>>>>>>). "
        "Resolve the conflict by choosing or combining the two sides, then delete the "
        "markers — committing them replaces working code with something that parses in "
        "almost no language."
    )
