"""A stack of pull requests, landed one step a call in the order GitHub keeps it open (#366).

A stack is pull requests each opened into the branch of the one below it, the lowest into the
trunk. A project landed its stacks with a command of its own: retarget the next pull request,
rebase, push with `--force-with-lease`, `gh pr merge` in a loop, `git worktree remove --force`.
The merge gate refuses a merge in a loop (decision 0040), and the forced removal skipped what the
plugin keeps a tree for. So the plugin owns the order, and leaves every act to the session:

1. the next pull request is moved onto the trunk BEFORE the one below it merges with its branch
   deleted, or GitHub closes it and never reopens it (#293, #329);
2. the lowest merges, from its own tree, one `gh pr merge` a call, judged as any merge is;
3. each pull request above is rebased off the squashed commits onto the trunk, the project's
   checks run on the result (the pre-push hook, where it is installed), and it is pushed with a
   lease on the commit GitHub has;
4. the landed one's tree is removed without `--force`, the way the plugin removes its own.

`next_step` reads GitHub (`gh`) and git, and says the one command that comes next; the planner
runs none of them. A merge run in this process would get no PostToolUse, so the founder's word
would never be spent nor the pull request settled (decision 0038). What it cannot read from
GitHub it keeps in Tier B: the commit each landing pull request stood at, so the rebase can cut
exactly those commits off the ones above.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import ci, config, store
from .gitctx import GitContext, GitError, _run

STATE = "stack-landing.json"
_LIST_FIELDS = "number,headRefName,baseRefName,headRefOid,isDraft"


@dataclass(frozen=True)
class Pull:
    """One open pull request as GitHub has it."""

    number: int
    head: str
    base: str
    oid: str
    draft: bool = False


@dataclass
class Step:
    """What comes next: why, and the one command to run; none where the stack is landed or the
    planner cannot go on (`blocked`)."""

    said: str
    run: str = ""
    blocked: bool = False
    extra: list[str] = field(default_factory=list)


def _gh(ctx: GitContext, *args: str) -> str | None:
    """`gh`'s output, or None where it is missing or failed."""
    if not shutil.which("gh"):
        return None
    try:
        proc = subprocess.run(["gh", *args], cwd=str(ctx.worktree_root), capture_output=True,
                              encoding="utf-8", errors="surrogateescape", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def open_pulls(ctx: GitContext) -> list[Pull] | None:
    """Every open pull request of this repository; None where `gh` cannot say."""
    out = _gh(ctx, "pr", "list", "--state", "open", "--limit", "200", "--json", _LIST_FIELDS)
    try:
        rows = json.loads(out) if out is not None else None
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return [Pull(int(row.get("number") or 0), str(row.get("headRefName") or ""),
                 str(row.get("baseRefName") or ""), str(row.get("headRefOid") or ""),
                 bool(row.get("isDraft"))) for row in rows if isinstance(row, dict)]


def _state_of(ctx: GitContext, number: int) -> str:
    return (_gh(ctx, "pr", "view", str(number), "--json", "state", "--jq", ".state") or "").strip()


def merge_flag(ctx: GitContext) -> str:
    """How this repository merges: `--squash` where it allows it, else `--merge`, else `--rebase`."""
    out = _gh(ctx, "repo", "view", "--json", "squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed")
    try:
        allowed = json.loads(out) if out else {}
    except ValueError:
        allowed = {}
    for key, flag in (("squashMergeAllowed", "--squash"), ("mergeCommitAllowed", "--merge"),
                      ("rebaseMergeAllowed", "--rebase")):
        if allowed.get(key):
            return flag
    return "--squash"


def _plans(ctx: GitContext) -> dict[str, dict]:
    data = store.read_json(store.tier_b(ctx, STATE), default={})
    return data if isinstance(data, dict) else {}


def _keep(ctx: GitContext, number: int, plan: dict | None) -> None:
    with store.guarded_json(store.tier_b(ctx, STATE), default={}) as box:
        plans = box[0] if isinstance(box[0], dict) else {}
        if plan is None:
            plans.pop(str(number), None)
        else:
            plans[str(number)] = plan
        box[0] = plans


def _trees(ctx: GitContext) -> dict[str, Path]:
    from . import worktree

    try:
        return worktree.trees_by_branch(ctx)
    except GitError:
        return {}


def _main(ctx: GitContext) -> Path:
    from . import worktree

    try:
        return worktree.main_checkout(ctx) or ctx.worktree_root
    except GitError:
        return ctx.worktree_root


def _git(ctx: GitContext, *args: str) -> str:
    return _run(list(args), ctx.worktree_root, check=False).strip()


def _holds(ctx: GitContext, branch: str, commit: str) -> bool | None:
    """Does the local `branch` still carry `commit`? None where the commit is not here."""
    if not _git(ctx, "cat-file", "-t", commit):
        return None
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, f"refs/heads/{branch}"],
                          cwd=str(ctx.worktree_root), capture_output=True, timeout=60)
    return proc.returncode == 0


def next_step(ctx: GitContext, trunk: str) -> Step:
    """The one thing to run next to land the stack this branch is in, or the lowest one open."""
    pulls = open_pulls(ctx)
    if pulls is None:
        return Step("`gh` could not list this repository's open pull requests, so no step is "
                    "known: `gh auth status` says why.", blocked=True)
    by_number = {pull.number: pull for pull in pulls}
    for key in sorted(_plans(ctx), key=lambda k: int(k) if k.isdigit() else 0):
        plan = _plans(ctx)[key]
        step = _owed(ctx, trunk, int(key), plan, by_number)
        if step is not None:
            return step
    bottom = _bottom(pulls, trunk, ctx.branch)
    if bottom is None:
        return Step("no stack is open here: no pull request into the trunk has another opened "
                    "into its branch. A single pull request merges as one: `gh pr merge <n>`.")
    above = [pull.number for pull in pulls if pull.base == bottom.head]
    _keep(ctx, bottom.number, {"branch": bottom.head, "oid": bottom.oid, "into": trunk,
                               "above": above})
    return _owed(ctx, trunk, bottom.number, _plans(ctx)[str(bottom.number)], by_number) or Step(
        "the stack is landed.")


def _bottom(pulls: list[Pull], trunk: str, branch: str) -> Pull | None:
    """The lowest pull request of a stack: into the trunk, with another opened into its branch.
    The stack `branch` is in first, else the lowest number."""
    bottoms = [pull for pull in pulls if pull.base == trunk
               and any(other.base == pull.head for other in pulls)]
    if not bottoms:
        return None

    def holds_branch(bottom: Pull) -> bool:
        walk, seen = [bottom.head], set()
        while walk:
            head = walk.pop()
            if head == branch:
                return True
            seen.add(head)
            walk += [p.head for p in pulls if p.base == head and p.head not in seen]
        return False

    return min(bottoms, key=lambda pull: (not holds_branch(pull), pull.number))


@dataclass(frozen=True)
class _Landing:
    """One pull request of a stack being landed: its number, the branch it merges from, the
    base it merges into, the commit it stood at, and the open pull requests above it."""

    number: int
    branch: str
    into: str
    oid: str
    above: list[Pull]


def _owed(ctx: GitContext, trunk: str, number: int, plan: dict,
          by_number: dict[int, Pull]) -> Step | None:
    """What the landing of pull request `number` still owes, or None once it owes nothing."""
    landing = _Landing(number, str(plan.get("branch") or ""), str(plan.get("into") or trunk),
                       str(plan.get("oid") or ""),
                       [by_number[n] for n in plan.get("above", []) if n in by_number])
    pull = by_number.get(number)
    if pull is not None:
        return _before_the_merge(ctx, landing, pull, plan)
    step = (_closed_unmerged(ctx, number) or _rebase_owed(ctx, landing)
            or _push_owed(ctx, landing) or _tree_owed(ctx, landing))
    if step is None:
        _keep(ctx, number, None)
    return step


def _closed_unmerged(ctx: GitContext, number: int) -> Step | None:
    """A landing pull request GitHub closed without merging: nothing above it is rebased."""
    state = _state_of(ctx, number)
    if not state or state == "MERGED":
        return None
    return Step(f"#{number} is {state.lower()}, not merged, so nothing above it is rebased off "
                f"it: `gh pr view {number}`.", blocked=True)


def _before_the_merge(ctx: GitContext, landing: _Landing, pull: Pull, plan: dict) -> Step:
    """The retarget each pull request above still needs, then the merge."""
    number, branch, into = landing.number, landing.branch, landing.into
    for above in landing.above:
        if above.base == branch:
            return Step(f"#{above.number} is opened into `{branch}`, which merging #{number} "
                        "deletes; GitHub would close it and never reopen it. Move it onto "
                        f"`{into}` first.", f"gh pr edit {above.number} --base {into}")
    if pull.draft:
        return Step(f"#{number} is a draft, which nothing merges: `gh pr ready {number}` once "
                    "its work is finished.", blocked=True)
    _keep(ctx, number, {**plan, "oid": pull.oid})
    tree = _trees(ctx).get(branch)
    where = f"cd {tree} && " if tree is not None else ""
    return Step(f"every pull request above #{number} is on `{into}`; merge #{number}, from its "
                "own tree so its checks are the ones judged.",
                f"{where}gh pr merge {number} {merge_flag(ctx)} --delete-branch")


def _rebase_owed(ctx: GitContext, landing: _Landing) -> Step | None:
    """A pull request above that still carries the landed one's commits, rebased off them."""
    trees, main, oid = _trees(ctx), _main(ctx), landing.oid
    for pull in landing.above if oid else []:
        holds = _holds(ctx, pull.head, oid)
        if holds is None:
            return Step(f"#{landing.number}'s last commit is not in this clone; fetch it to rebase "
                        "what is above it.", f"git -C {main} fetch origin pull/{landing.number}/head")
        if not holds:
            continue
        tree = trees.get(pull.head)
        if tree is None:
            made = main / ".claude" / "worktrees" / pull.head.replace("/", "-")
            return Step(f"#{pull.number} still carries #{landing.number}'s commits, and "
                        f"`{pull.head}` is checked out in no tree here.",
                        f"git -C {main} worktree add {made} {pull.head}")
        return Step(f"#{landing.number} landed; #{pull.number} still carries its commits under "
                    f"the squash. Rebase it onto `{landing.into}`, past them.",
                    f"git -C {tree} fetch origin {landing.into} && git -C {tree} rebase --onto "
                    f"origin/{landing.into} {oid} {pull.head}")
    return None


def _push_owed(ctx: GitContext, landing: _Landing) -> Step | None:
    """A pull request above rebased here whose commits GitHub does not have yet."""
    trees, main = _trees(ctx), _main(ctx)
    for pull in landing.above:
        local = _git(ctx, "rev-parse", "--verify", "--quiet", f"refs/heads/{pull.head}")
        if local and pull.oid and local != pull.oid:
            tree = trees.get(pull.head) or main
            return Step(f"#{pull.number} is rebased here and GitHub still has the old commits." +
                        _checks_note(ctx), f"git -C {tree} push --force-with-lease="
                        f"{pull.head}:{pull.oid} origin {pull.head}")
    return None


def _tree_owed(ctx: GitContext, landing: _Landing) -> Step | None:
    """The landed pull request's tree, removed the way the plugin removes its own."""
    tree, main = _trees(ctx).get(landing.branch), _main(ctx)
    if tree is None or tree == main:
        return None
    return Step(f"#{landing.number} landed and its tree is still here; removed without "
                "`--force`, it keeps anything it holds that nothing else has.",
                f"git -C {main} worktree remove {tree}")


def _checks_note(ctx: GitContext) -> str:
    """Where the project's checks run on the rebased commit before it is pushed."""
    if ci.installed(ctx):
        return " The pre-push hook runs the project's checks on it as it goes."
    command = " ".join(config.load(ctx).test_command) or "the project's checks"
    return (f" No pre-push hook runs the project's checks here (`claude-bp-ci local` installs "
            f"one): run `{command}` in its tree first.")
