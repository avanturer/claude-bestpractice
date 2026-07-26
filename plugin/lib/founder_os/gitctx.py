"""Git context resolution.

The harness is the source of truth for worktree existence; this module only reads.
Never shells out to anything that mutates the repository.

Worktrees share a git common directory. That property is what makes cross-session
coordination possible at all, so `common_dir` is the anchor for every shared path.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git invocation fails or the cwd is not a repository."""


def _run(args: list[str], cwd: Path | str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


@dataclass(frozen=True)
class GitContext:
    """Everything about the repository a gate needs, resolved once.

    worktree_root
        Top level of *this* checkout. Differs per worktree.
    common_dir
        Shared `.git` directory. Identical across every worktree of one clone, which
        is why Tier B lives under it: shared by siblings, invisible to git, dies with
        the clone.
    head
        Commit SHA at resolution time. Empty string on an unborn branch, which is a
        legitimate state for a fresh repository and must not raise.
    """

    worktree_root: Path
    common_dir: Path
    head: str
    branch: str
    is_worktree: bool

    @property
    def repo_key(self) -> str:
        """Stable identity for this clone, shared by all its worktrees."""
        return self.common_dir.resolve().as_posix()


def resolve(cwd: Path | str | None = None) -> GitContext:
    cwd = Path(cwd or os.getcwd())
    if not cwd.exists():
        raise GitError(f"cwd does not exist: {cwd}")

    try:
        worktree_root = Path(_run(["rev-parse", "--show-toplevel"], cwd))
    except GitError as exc:
        raise GitError(f"not inside a git repository: {cwd}") from exc

    common = Path(_run(["rev-parse", "--git-common-dir"], cwd))
    if not common.is_absolute():
        common = (worktree_root / common).resolve()

    git_dir = Path(_run(["rev-parse", "--git-dir"], cwd))
    if not git_dir.is_absolute():
        git_dir = (worktree_root / git_dir).resolve()

    # An unborn branch has no HEAD commit. That is normal for a fresh repo.
    # `--verify` is what makes an unborn branch return nothing instead of echoing back
    # the literal string "HEAD". Without it every "has this repo any history?" test read
    # true in a repository with zero commits, and the docstring above was simply false.
    head = _run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd, check=False)
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd, check=False) or "HEAD"

    return GitContext(
        worktree_root=worktree_root.resolve(),
        common_dir=common,
        head=head,
        branch=branch,
        is_worktree=git_dir.resolve() != common.resolve(),
    )


def changed_files(ctx: GitContext, since: str | None = None) -> list[str]:
    """Repo-relative paths changed since `since`, or uncommitted if `since` is None.

    Includes untracked files: an agent that creates a file and never stages it has
    still changed the working tree, and the scope-drift check must see it.
    """
    # -c core.quotePath=false, or git C-quotes any path outside ASCII: `src/é.py` comes
    # back as `"src/\303\251.py"`, which then matches no file on disk. Every downstream
    # check reads that as "deleted" and stops applying — so a failing suite passed the
    # gate purely because a filename had an accent in it. An ASCII control proved the
    # quoting was the cause rather than the test.
    quiet = ["-c", "core.quotePath=false"]
    out: set[str] = set()
    if since:
        diff = _run(quiet + ["diff", "--name-only", f"{since}..HEAD"], ctx.worktree_root, check=False)
        out.update(p for p in diff.splitlines() if p)

    for args in (["diff", "--name-only", "HEAD"], ["diff", "--name-only", "--cached"]):
        out.update(p for p in _run(quiet + args, ctx.worktree_root, check=False).splitlines() if p)

    untracked = _run(
        quiet + ["ls-files", "--others", "--exclude-standard"], ctx.worktree_root, check=False
    )
    out.update(p for p in untracked.splitlines() if p)
    return sorted(out)


def stash_baseline(ctx: GitContext) -> str:
    """A SHA covering HEAD plus the dirty working tree, without touching the tree.

    `git stash create` builds the commit objects and prints the SHA but does not
    modify the index, the working tree, or the stash reflog. Returns HEAD when the
    tree is clean (stash create prints nothing in that case).
    """
    sha = _run(["stash", "create"], ctx.worktree_root, check=False)
    # VALIDATE, never trust the stdout. Mid-merge and mid-rebase `stash create` refuses
    # and prints its refusal, which was then stored as the session's baseline — a
    # baseline that resolves to nothing makes every diff empty, so the Stop gate saw no
    # changes and allowed every finish for the rest of that session. Silently.
    if sha and _run(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], ctx.worktree_root, check=False):
        return sha
    return ctx.head


def resolve_for_cli(cwd: Path | str | None = None) -> GitContext:
    """Resolve, or exit with a sentence instead of a stack trace.

    Being outside a repository is an ordinary thing to do — every command here is
    repo-scoped, and a traceback reads as "this tool is broken" rather than "you are in
    the wrong directory".
    """
    try:
        return resolve(cwd)
    except GitError as exc:
        raise SystemExit(f"founder-os: {exc}\nRun this inside a git repository.")


def blob_sha(ctx: GitContext, relpath: str) -> str | None:
    """Content hash of a tracked file, for provenance stamping.

    Content-addressed, never mtime: creating a worktree or checking out a branch
    resets mtimes and would invalidate every cached claim at once.
    """
    out = _run(["hash-object", "--", relpath], ctx.worktree_root, check=False)
    return out or None


def is_ancestor(ctx: GitContext, maybe_ancestor: str, descendant: str = "HEAD") -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", maybe_ancestor, descendant],
        cwd=str(ctx.worktree_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


def worktree_paths(ctx: GitContext) -> list[Path]:
    """Every registered worktree of this clone.

    Used to validate a session record: a live pid is not enough, the worktree must
    still be registered. A session whose worktree was removed is dead even if some
    unrelated process inherited its pid.
    """
    out = _run(["worktree", "list", "--porcelain"], ctx.worktree_root, check=False)
    return [
        Path(line[len("worktree ") :]).resolve()
        for line in out.splitlines()
        if line.startswith("worktree ")
    ]
