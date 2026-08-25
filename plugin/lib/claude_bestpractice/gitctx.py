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
        # NOT text=True. That decodes as strict UTF-8, and a filename is a byte string
        # on POSIX, not text — one file named in latin-1 anywhere in the repository made
        # `git diff --name-only` raise UnicodeDecodeError inside the fail-closed Stop
        # gate. Every finish was then refused, identically, forever, with a message about
        # a codec; no config setting escaped it and no amount of re-running helped.
        #
        # surrogateescape, not replace: it round-trips. The undecodable bytes come back
        # as lone surrogates, and because Python's own filesystem encoding uses the same
        # error handler, `open()` and `Path.stat()` on that string reach the real file.
        # `replace` would substitute U+FFFD and every downstream path check would read
        # the file as deleted — which is how a failing suite passed the gate once already.
        encoding="utf-8",
        errors="surrogateescape",
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

    @property
    def repo_name(self) -> str:
        """What to call this repository, identically from every one of its worktrees.

        The worktree directory name is the wrong answer and was the one being printed:
        one repository showed up as `fuddy` in the main checkout and `fuddy-envfix` in a
        worktree, reading as two repositories in a product whose whole stated scene is
        three to eight worktrees of one. The state was correctly shared the whole time —
        only the label lied.

        Derived from the common dir, which every worktree of a clone agrees on.
        """
        common = self.common_dir.resolve()
        if common.name == ".git":
            return common.parent.name
        # A bare clone, or `--separate-git-dir`: the directory is `fuddy.git` itself.
        return common.name[:-4] if common.name.endswith(".git") else common.name


def resolve(cwd: Path | str | None = None) -> GitContext:
    cwd = Path(cwd or os.getcwd())
    if not cwd.exists():
        raise GitError(f"cwd does not exist: {cwd}")
    try:
        worktree_root = Path(_run(["rev-parse", "--show-toplevel"], cwd))
    except GitError as exc:
        raise GitError(f"not inside a git repository: {cwd}") from exc

    # Joined to the directory git was ASKED FROM, never to the top level. `--git-common-dir`
    # answers relative to the current directory, and `--show-toplevel` answers absolutely,
    # so joining one to the other only agrees while the two are the same directory. From
    # the repository root they are, which is why this stood for fifty releases; one `cd`
    # into a subdirectory and the answer walked out of the repository by exactly the depth
    # of that subdirectory. `cd backend/src/fuddy/merge` in a repository at
    # `/home/<user>/dev/fuddy` resolved the common dir to `/home/.git` — four levels up —
    # and the shell `cd` persists, so every later call resolved it there too (#187).
    #
    # Both harms come from this one line, and the quiet one is worse. Where that path is
    # unwritable the gate raised PermissionError and, being fail-closed, refused every
    # tool call for the rest of the session — with no way back, because the `cd` that
    # would fix it is itself a refused Bash call. Where it happens to be writable nothing
    # fails at all: Tier B moves to a directory no sibling session reads, so the board,
    # the leases and the observed test runs are written to a second store that looks
    # exactly like an empty repository. `is_worktree` flips too — it compares these two
    # paths — so a main checkout starts reporting itself as a worktree.
    common = Path(_run(["rev-parse", "--git-common-dir"], cwd))
    if not common.is_absolute():
        common = (cwd / common).resolve()

    # The same anchor, though nothing can reach the difference today: `--git-dir` answers
    # relatively only when the current directory IS the top level, and there the two
    # anchors are the same directory. Verified against git rather than assumed — from any
    # subdirectory, and from a worktree at any depth, it answers absolutely. Corrected
    # anyway, because leaving one join measured from the wrong place is leaving the next
    # person to rediscover which of the two was the safe one.
    git_dir = Path(_run(["rev-parse", "--git-dir"], cwd))
    if not git_dir.is_absolute():
        git_dir = (cwd / git_dir).resolve()

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


def _authored_floor(ctx: GitContext, since: str) -> str:
    """`since`, raised past work that arrived from upstream rather than from this session.

    A fast-forward is not an edit. `git pull --ff-only` moved a local trunk past eighteen
    commits other sessions had already merged, and every file in them was reported as this
    session's scope drift — with "revert what is out of scope" as the advice, which
    followed literally means rewinding other people's merged work. A gate whose remedy is
    destructive on its own false positive is worse than no gate (#71).

    The floor becomes the merge base with the trunk ONLY when upstream has genuinely moved
    past the baseline, so a session that branched long before it started still has its own
    work measured from where it started rather than from the branch point.
    """
    trunk = ""
    try:
        from .gitpolicy import default_branch

        trunk = default_branch(ctx)
    except Exception:  # noqa: BLE001 - an unknown trunk is not a reason to lose the diff
        return since
    # The REMOTE trunk, never the local one. Work arrives from other sessions by being
    # pushed and pulled, so `origin/<trunk>` is what "somebody else's, already merged"
    # means. The local trunk is a branch this session may be committing to itself, and
    # measuring against it erases the session's own work: merge-base(main, HEAD) is HEAD
    # for a session working on main, so the diff came back empty and every gate that reads
    # it stopped firing. Caught by the escalation-ceiling tests, which went to zero blocks.
    base = _run(["merge-base", f"origin/{trunk}", "HEAD"], ctx.worktree_root, check=False).strip()
    if not base or base == since:
        return since
    # An ancestor test, not a comparison: the floor rises only when upstream has genuinely
    # moved past where this session started. A session that branched long before it began
    # still measures from its own baseline rather than from the branch point.
    ahead = subprocess.run(
        ["git", "merge-base", "--is-ancestor", since, base],
        cwd=str(ctx.worktree_root), capture_output=True, timeout=30,
    )
    return base if ahead.returncode == 0 else since


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
        floor = _authored_floor(ctx, since)
        diff = _run(quiet + ["diff", "--name-only", f"{floor}..HEAD"], ctx.worktree_root, check=False)
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
        raise SystemExit(f"claude-bestpractice: {exc}\nRun this inside a git repository.")


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
        encoding="utf-8",
        errors="surrogateescape",
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
