"""Getting work out: merge state, pull requests, and what the founder actually sees.

Three things the rest of this plugin left to the agent's judgement, which is the same as
leaving them undone.

**Merge conflicts.** Git leaves the repository in a state that reads as normal — files
on disk, a branch checked out — while `MERGE_HEAD` exists and half the tree is markers.
A session that starts there and is not told will commit the markers, and the pre-write
check only catches the ones it writes itself.

**Pull requests.** The founder is told never to read a diff, so a PR body listing files
changed is worthless to them. The body here is built from the work ledger and the
decision records: what was asked, what was decided, what was tried and abandoned. The
diff is one line at the bottom for whoever wants it.

**What shipped.** The stated operating mode is that the founder looks at outcomes — a
number, a preview, a working feature — and never at code. Nothing in this plugin produced
that view; every surface it had was for the agent. `claude-bp ship` is the one that
faces the other way.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .gitctx import GitContext


@dataclass
class MergeState:
    in_progress: bool = False
    conflicted: list[str] = field(default_factory=list)
    kind: str = ""

    def render(self) -> str:
        if not self.in_progress:
            return ""
        if not self.conflicted:
            return (
                f"A {self.kind} is in progress with no conflicts left. "
                f"Finish it: `git {self.kind} --continue`, or abandon it: `git {self.kind} --abort`."
            )
        shown = ", ".join(self.conflicted[:6])
        more = f" (+{len(self.conflicted) - 6} more)" if len(self.conflicted) > 6 else ""
        return (
            f"UNRESOLVED {self.kind.upper()}: {shown}{more}\n"
            "Each of these has both sides in the file with conflict markers. Resolve by "
            "choosing or combining — never by deleting one side blind — then `git add` it. "
            f"`git {self.kind} --abort` puts the repository back if this is not your work."
        )


# What actually means "half-finished", verified against git 2.43 rather than assumed.
#
# `REBASE_HEAD` is deliberately NOT here, and that is the whole point of this table.
# Git LEAVES IT BEHIND after `git rebase --continue` succeeds: resolve a conflict, finish
# the rebase, and the file is still sitting in the git dir on a completely clean tree.
# Treating it as a marker meant the single most ordinary recovery in this founder's
# workflow — rebase, hit a conflict, resolve it, continue — permanently convinced the
# plugin that a rebase was unfinished. `claude-bp ship` then refused forever, `ready()`
# blocked every pull request, and every session opened with UNRESOLVED REBASE in its
# board. Nothing clears it, so the repository never recovers on its own.
#
# The directories are the real markers: git creates them when a rebase starts and removes
# them when it ends, both of which were observed. `rebase-apply` covers `git rebase
# --apply` and `git am`, which the directory check missed entirely.
_IN_PROGRESS = (
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
)


def merge_state(ctx: GitContext) -> MergeState:
    """Whether a merge, rebase or cherry-pick is half-finished, and what is unresolved.

    Read from the git directory rather than from `git status` output, which is localised
    and reformatted between versions.
    """
    git_dir = _git_dir(ctx)
    kind = next((name for marker, name in _IN_PROGRESS if (git_dir / marker).exists()), "")
    if not kind:
        return MergeState()

    listed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    ).stdout
    return MergeState(True, [p for p in listed.splitlines() if p], kind)


def _git_dir(ctx: GitContext) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    ).stdout.strip()
    return Path(out) if out else ctx.common_dir


def commits_since(ctx: GitContext, base: str, head: str = "HEAD") -> list[str]:
    """Subject lines a branch adds over `base`, oldest first.

    `head` is named when a merge is being judged from somewhere else: counting on the
    session's HEAD reported "no commits on top of main" for a pull request with twenty of
    them, because the session was standing in the main checkout, which is not supposed to
    carry any (#74).
    """
    if not base:
        return []
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%s", f"{base}..{head or 'HEAD'}"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def diffstat(ctx: GitContext, base: str) -> str:
    if not base:
        return ""
    out = subprocess.run(
        ["git", "diff", "--shortstat", f"{base}..HEAD"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    return out.stdout.strip()


def shipped(ctx: GitContext, base: str) -> str:
    """What changed, in the terms the founder cares about — never a diff.

    Built from the work ledger, the decisions and the dead-end record rather than from
    the commit log alone, because "what was delivered" and "what was committed" are not
    the same question and only the first one is worth a founder's attention.
    """
    from . import evidence

    lines = _work_sections(ctx)

    lines.append("")
    lines.append(_test_health(ctx))

    stat = diffstat(ctx, base)
    if stat:
        lines.append(f"({stat} — the code, if you want it)")
    return "\n".join(lines)


def _test_health(ctx: GitContext) -> str:
    """Observed green, observed red, or never observed — three states, not two.

    Reporting "Tests: green." from the ABSENCE of a red record turns no-evidence into a
    positive assertion, on the one surface built for a founder who reads no code. That
    is decision 0002 inverted by the thing that quotes it.
    """
    from . import evidence

    red = evidence.red_line(ctx)
    if red:
        return red
    if evidence.last_green(ctx):
        return "Tests: green (observed by the gate)."
    return "Tests: NEVER RUN here — nothing has verified this."


def _section(heading: str, items: list[str], bullet: str = "  - ") -> list[str]:
    """A heading and its bullets, or nothing at all when there is nothing to say."""
    return [heading] + [f"{bullet}{item}" for item in items] if items else []


def _work_sections(ctx: GitContext) -> list[str]:
    """Delivered, in flight, decided, ruled out — the four a founder asks about."""
    from . import attempts, knowledge, plan

    return (
        _section("DELIVERED", [t.title for t in plan.load_all(ctx, plan.DONE)[-8:]])
        + _section("IN FLIGHT", [t.title for t in plan.load_all(ctx, plan.DOING)[:5]])
        + _section(
            "DECIDED",
            [p.stem.split("-", 1)[-1].replace("-", " ") for p in knowledge.decision_files(ctx)[-4:]],
        )
        + _section(
            "RULED OUT BY TRYING",
            [f"{a.title}: {a.why.splitlines()[0][:90]}" for a in attempts.load_all(ctx)[-3:]],
        )
    )


def pr_body(ctx: GitContext, base: str) -> str:
    """A pull request body written for a reader who does not read diffs."""
    from . import attempts, knowledge, plan

    tasks = plan.load_all(ctx, plan.DONE) + plan.load_all(ctx, plan.DOING)
    what = [t.title for t in tasks[-8:]] or commits_since(ctx, base)[:8] or ["(no commits yet)"]

    lines = ["## What this does", ""] + [f"- {item}" for item in what]
    lines += _section(
        "\n## Decisions taken\n",
        [f"`{p.name}` — {p.stem.split('-', 1)[-1].replace('-', ' ')}"
         for p in knowledge.decision_files(ctx)[-4:]],
        bullet="- ",
    )
    lines += _section(
        "\n## Considered and rejected\n",
        [f"{a.title} — {a.why.splitlines()[0][:120]}" for a in attempts.load_all(ctx)[-4:]],
        bullet="- ",
    )
    return "\n".join(lines + ["", "---", diffstat(ctx, base), ""])



def unverified_on(ctx: GitContext, branch: str) -> bool:
    """Unverified finishes recorded on a named branch. See `_unverified_here`."""
    return any(
        isinstance(r, dict) and r.get("branch") == branch
        for r in store.read_jsonl(store.tier_b(ctx, "unverified.jsonl"))
    )


def _unverified_here(ctx: GitContext) -> bool:
    """Unverified finishes recorded on THIS branch. Tier B is shared by every worktree.

    The check read the whole file, and the file is clone-wide: one session finishing
    without proof on `feat/a` blocked the pull request for `feat/b`, `feat/c` and every
    branch opened afterwards, forever, since nothing removes the record. Meanwhile the
    message said "this branch", which is how it survived being read.
    """
    records = store.read_jsonl(store.tier_b(ctx, "unverified.jsonl"))
    return any(
        isinstance(r, dict) and r.get("branch") == ctx.branch
        for r in records
    )


def ready(ctx: GitContext, base: str) -> list[str]:
    """Reasons this branch is not ready to open a pull request. Empty when it is."""
    from . import evidence

    problems: list[str] = []
    state = merge_state(ctx)
    if state.in_progress:
        problems.append(f"a {state.kind} is unfinished")
    if not commits_since(ctx, base):
        problems.append(f"no commits on top of {base}")
    if evidence.red(ctx):
        problems.append("the test suite is red")
    if not evidence.last_green(ctx):
        problems.append("no test run has ever been observed on this branch")
    if _unverified_here(ctx):
        problems.append("this branch carries an UNVERIFIED finish")

    if _dirty(ctx):
        problems.append("there are uncommitted changes")
    return problems


# The plugin's own bookkeeping is not the founder's unfinished work. `.claude/` holds the
# stage marker, the green ledger and the config, all written by the gates themselves and
# all untracked in a repository that has never committed them — so every session made the
# tree read as dirty within seconds of starting, and this check reported it as a reason
# not to ship. The evidence gate exempts the same prefix for the same reason.
_NOT_THE_FOUNDERS = (".claude/",)


def _dirty(ctx: GitContext) -> bool:
    """Uncommitted work that would not reach the remote, ignoring the gates' own state."""
    listed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    ).stdout
    for line in listed.splitlines():
        # `XY <path>`, and for a rename `XY <old> -> <new>`. The destination is what a
        # later commit would carry, so that is the one judged.
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path and not path.startswith(_NOT_THE_FOUNDERS):
            return True
    return False
