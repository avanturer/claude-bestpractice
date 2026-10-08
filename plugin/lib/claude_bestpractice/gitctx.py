"""Git context resolution.

The harness is the source of truth for worktree existence; this module only reads.
Never shells out to anything that mutates the repository: the one thing it writes is a
session's baseline, as loose objects no ref points at — never a ref, the index or a file.

Worktrees share a git common directory. That property is what makes cross-session
coordination possible at all, so `common_dir` is the anchor for every shared path.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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


def _status(args: list[str], cwd: Path | str) -> tuple[int, str]:
    """A git call whose FAILURE is data: (exit status, stdout).

    `_run(check=False)` answers "" for a command that failed and for one that found
    nothing, and those are opposite facts about a diff — one means no change, the other
    means the question could not be asked.
    """
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    return proc.returncode, proc.stdout.strip()


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


def authored_floor(ctx: GitContext, since: str) -> str:
    """`since`, raised past work that arrived from elsewhere rather than from this session.

    A fast-forward is not an edit. `git pull --ff-only` moved a local trunk past eighteen
    commits other sessions had already merged, and every file in them was reported as this
    session's scope drift — with "revert what is out of scope" as the advice, which
    followed literally means rewinding other people's merged work. A gate whose remedy is
    destructive on its own false positive is worse than no gate (#71).

    Elsewhere is any remote branch but this branch's own (decision 0030). The trunk was the
    only one asked, so a session that fast-forwarded onto another pull request's branch to
    build on it had every file of that branch read as its own, and a stub in one of them
    refused its finish as "introduced in this turn" (#258).

    The floor is the commit this session's own commits stand on — the ones no other remote
    branch carries — and it rises ONLY past where the session started, so a session that
    branched long before it began still has its own work measured from its own baseline
    rather than from the branch point.
    """
    start = start_of(ctx, since)
    arrived = _arrived_from(ctx)
    stands_on = _stands_on(ctx, start, arrived) if start and ctx.head and arrived else []
    # An ancestor test, not a comparison: the floor rises only past where this session
    # started. Asked of the commit HEAD stood on, never of the baseline itself. A tree dirty
    # at session start has a `git stash create` commit for a baseline, which is on no branch
    # and so the ancestor of nothing: the floor never rose, and a main checkout that only
    # fast-forwarded over ninety-four merged commits had every file in them read as this
    # session's work — stubs "introduced in this turn" included (#255).
    rising = [commit for commit in dict.fromkeys(stands_on)
              if commit != start and is_ancestor(ctx, start, commit)]
    return _highest(ctx, start, rising) or since


def _stands_on(ctx: GitContext, start: str, arrived: list[str]) -> list[str]:
    """The commits this session's own commits stand on: HEAD itself when it made none.

    Its own are what HEAD carries since `start` that none of `arrived` carries, and what they
    stand on are the `-` lines of the same walk. One walk, however many branches the remote
    holds, because this runs twice in every Stop. Empty when git cannot say.

    `--ignore-missing`, because a sibling's `git fetch --prune` can delete a remote branch
    between listing it and walking it, and one name git no longer knows would otherwise fail
    the walk and take the trunk's step with it.
    """
    proc = subprocess.run(
        ["git", "rev-list", "--ignore-missing", "--boundary", "HEAD", f"^{start}", "--stdin"],
        input="".join(f"^{ref}\n" for ref in arrived),
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    if proc.returncode != 0:
        return []
    listed = proc.stdout.split()
    stands_on = [line[1:] for line in listed if line.startswith("-")]
    return stands_on if len(stands_on) < len(listed) else [ctx.head]


def _arrived_from(ctx: GitContext) -> list[str]:
    """The remote branches whose commits are somebody else's work when this branch carries them.

    Every one but this branch's own remote copy, the one of the same name, which is where the
    session's own work goes when it is pushed: counted as arrived, a session's commits left
    its diff the moment it pushed them, and every gate that reads the diff stopped seeing
    them. That is the widening #258 was left open over.

    The same name, never the configured upstream. A branch cut from a remote branch tracks
    that branch, so `git checkout -b mine origin/theirs` — the ordinary way to build on
    another pull request — would make theirs the session's own, and a branch cut from
    `origin/main` would make the trunk its own.

    The trunk is never its own, even when it is the branch checked out. Work lands there by
    being pushed and pulled, which is what "somebody else's, already merged" means; the LOCAL
    trunk is the branch a session on it commits to. With no branch checked out nothing names a
    remote copy as the session's own, so the trunk is the only one asked, as it was before.

    Local branches are never asked. `git branch backup` before a risky rebase would otherwise
    make the session's whole history somebody else's.
    """
    root = ctx.worktree_root
    remote = _run(["for-each-ref", "--format=%(refname)", "refs/remotes"], root, check=False).split()
    if not remote:
        return []
    try:
        from .gitpolicy import default_branch

        trunk = default_branch(ctx)
    except Exception:  # noqa: BLE001 - an unknown trunk is not a reason to lose the diff
        trunk = ""
    names = _run(["remote"], root, check=False).split()
    branch = "" if ctx.branch in ("", "HEAD") else ctx.branch
    if not branch:
        return [ref for ref in remote if trunk and ref in _copies(names, trunk)]
    own = set() if branch == trunk else _copies(names, branch)
    return [ref for ref in remote if ref not in own]


def _copies(remotes: list[str], branch: str) -> set[str]:
    """The remote-tracking refs a branch of this name has, one per remote."""
    return {f"refs/remotes/{name}/{branch}" for name in remotes}


def _highest(ctx: GitContext, start: str, commits: list[str]) -> str:
    """The one commit of `commits` that every other one is an ancestor of; "" for none.

    Two lines of somebody else's work joined by a merge, neither inside the other, have no
    such commit, and one floor can step over only one of them. It steps over the longer, so
    the diff carries as little of anyone else's work as a single floor can.
    """
    if len(commits) < 2:
        return commits[0] if commits else ""
    tips = _run(["merge-base", "--independent", *commits], ctx.worktree_root, check=False).split()
    if len(tips) < 2:
        return tips[0] if tips else ""
    return max(tips, key=lambda tip: _count(ctx, f"{start}..{tip}"))


def _count(ctx: GitContext, revisions: str) -> int:
    """How many commits `git rev-list` lists for `revisions`; 0 when it cannot say."""
    said = _run(["rev-list", "--count", revisions], ctx.worktree_root, check=False)
    return int(said) if said.isdigit() else 0


def start_of(ctx: GitContext, since: str) -> str:
    """The commit HEAD stood on when the baseline `since` was taken.

    `since` itself, unless it is the commit `git stash create` makes of a dirty tree: that
    one's first parent is HEAD at the time and its second the index, filed as `index on …`.
    Read off the commit, so every baseline already recorded is understood as it is.
    """
    parents = _run(["rev-list", "--parents", "-n", "1", since], ctx.worktree_root,
                   check=False).split()
    if len(parents) < 3:
        return since
    index = _run(["log", "-1", "--format=%s", parents[2]], ctx.worktree_root, check=False)
    return parents[1] if index.startswith("index on ") else since


def _carried_from_the_start(ctx: GitContext, since: str, floor: str) -> set[str]:
    """Paths the tree was already carrying at session start, untouched by it since.

    Only asked once the floor has risen past a dirty start. The baseline then no longer
    subtracts what was dirty when the session began, so it is subtracted here: a path whose
    content is still what the baseline recorded, and that upstream left alone, is the tree
    the session was handed — in a main checkout shared by several sessions, someone else's.
    """
    start = since if floor == since else start_of(ctx, since)
    if start == since:
        return set()
    quiet = ["-c", "core.quotePath=false"]
    code_mine, mine = _status(quiet + ["diff", "--name-only", since], ctx.worktree_root)
    code_theirs, theirs = _status(quiet + ["diff", "--name-only", start, floor],
                                  ctx.worktree_root)
    if code_mine or code_theirs:
        return set()
    dirty_then = _status(quiet + ["diff", "--name-only", start, since], ctx.worktree_root)[1]
    return set(dirty_then.splitlines()) - set(mine.splitlines()) - set(theirs.splitlines())


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
    measured = False
    if since:
        floor = authored_floor(ctx, since)
        # The baseline against the WORKING TREE, in one diff: `floor..HEAD` answered for
        # the commits and the uncommitted scans below answered for everything else in the
        # tree — including files that were already dirty when the session started and have
        # not been touched since. In a main checkout shared by three to eight sessions that
        # is somebody else's work, and it was being counted as this session's: 335 files
        # it had never opened, with a scope-drift refusal and a demand for a card over them
        # (#213). The baseline commit already carries the dirty tree as it stood at session
        # start (`stash_baseline`), so diffing against it says what THIS session changed.
        code, diff = _status(quiet + ["diff", "--name-only", floor], ctx.worktree_root)
        measured = code == 0
        out.update(p for p in diff.splitlines() if p)
        out -= _carried_from_the_start(ctx, since, floor)

    # The baseline is a `git stash create` commit, and git prunes unreachable objects: a
    # baseline it can no longer resolve answers nothing at all. Falling through to the
    # working-tree scans is what keeps a session whose baseline has been collected visible
    # to every gate — the alternative is a diff that comes back empty and a Stop that
    # certifies a turn it never looked at.
    if not measured:
        for args in (["diff", "--name-only", "HEAD"], ["diff", "--name-only", "--cached"]):
            out.update(
                p for p in _run(quiet + args, ctx.worktree_root, check=False).splitlines() if p
            )

    untracked = _run(
        quiet + ["ls-files", "--others", "--exclude-standard"], ctx.worktree_root, check=False
    )
    already = _untracked_at(ctx, since) if measured else {}
    for rel in untracked.splitlines():
        # An untracked file that was already sitting there, byte for byte, when this
        # session started is not this session's change — it is whatever the tree happened
        # to be carrying, which in a shared main checkout is another session's work (#213).
        if rel and already.get(rel) != blob_sha(ctx, rel):
            out.add(rel)
    return sorted(out)


def _untracked_at(ctx: GitContext, since: str) -> dict[str, str]:
    """The untracked files the baseline captured, as path -> blob sha.

    `stash_baseline` files them in a third parent rather than in the commit's own tree, as
    `git stash push -u` does, so they are asked for by name. Empty for a baseline taken
    before 1.73.0 — which never held one, whatever it was asked for — or for a tree with
    none, in which case every untracked file reads as new, which is the behaviour this had
    always.

    By CONTENT and not by name: a file that was there and has since been rewritten is this
    session's change, and dropping it by name would hide it.
    """
    listing = _run(["ls-tree", "-r", f"{since}^3"], ctx.worktree_root, check=False)
    found: dict[str, str] = {}
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if path and len(parts) >= 3:
            found[path] = parts[2]
    return found


def stash_baseline(ctx: GitContext) -> str:
    """A SHA covering HEAD plus the dirty working tree, without touching the tree.

    `git stash create` builds the commit objects and prints the SHA but does not
    modify the index, the working tree, or the stash reflog. Returns HEAD when the
    tree is clean (stash create prints nothing in that case).

    The untracked files the tree was already carrying go in a third parent, built here.
    `stash create` takes a message and nothing else, so the `-u` this used to pass was
    filed as that message and no baseline ever held an untracked file: every one a shared
    main checkout was carrying read as this session's work, another session's scratch as
    scope drift against a session that never opened it (#213, #255).
    """
    sha = _run(["stash", "create"], ctx.worktree_root, check=False)
    # VALIDATE, never trust the stdout. Mid-merge and mid-rebase `stash create` refuses
    # and prints its refusal, which was then stored as the session's baseline — a
    # baseline that resolves to nothing makes every diff empty, so the Stop gate saw no
    # changes and allowed every finish for the rest of that session. Silently.
    if not (sha and _run(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
                         ctx.worktree_root, check=False)):
        sha = ""
    return _with_untracked(ctx, sha) or sha or ctx.head


# How much untracked content a baseline hashes. A session start that reads a gigabyte of
# somebody's data directory is a session start that times out, and past these the files
# are simply not recorded — which is how every baseline behaved before they ever were.
MAX_UNTRACKED_FILES = 2_000
MAX_UNTRACKED_BYTES = 64 * 1024 * 1024

# Who the baseline's commits say made them. They are objects no ref points at and nobody
# reads, and a machine with no identity configured must not lose its baseline over that.
_BASELINE_IDENTITY = {
    "GIT_AUTHOR_NAME": "claude-bestpractice", "GIT_AUTHOR_EMAIL": "baseline@claude-bestpractice",
    "GIT_COMMITTER_NAME": "claude-bestpractice",
    "GIT_COMMITTER_EMAIL": "baseline@claude-bestpractice",
}


def _with_untracked(ctx: GitContext, stash: str) -> str:
    """`stash` — or HEAD, when nothing tracked has changed — with the untracked files as a
    third parent, the shape `git stash push -u` gives them. "" when there are none, when
    there are more than a baseline records, or when git would not build it.

    Hashed through a throwaway index, so the tree's own index is never touched. Plumbing
    throughout: `git commit` would honour `commit.gpgSign`, and a session start waiting on
    a passphrase prompt is a session that never starts.
    """
    names = _untracked_names(ctx.worktree_root) if ctx.head else []
    if not names:
        return ""
    root, label = ctx.worktree_root, f"{ctx.branch}: {ctx.head[:7]}"
    env = {**os.environ, **_BASELINE_IDENTITY}
    untracked = _object(root, ["commit-tree", _tree_of(root, names, env),
                               "-m", f"untracked files on {label}"], env)
    index = f"{stash}^2" if stash else _object(
        root, ["commit-tree", f"{ctx.head}^{{tree}}", "-p", ctx.head, "-m", f"index on {label}"], env)
    if not (untracked and index):
        return ""
    message = (_run(["log", "-1", "--format=%B", stash], root, check=False) if stash else "") \
        or f"WIP on {label}"
    return _object(root, ["commit-tree", f"{stash or ctx.head}^{{tree}}", "-p", ctx.head,
                          "-p", index, "-p", untracked, "-m", message], env)


def _untracked_names(root: Path) -> list[str]:
    """The untracked files a baseline records, or none when there are more than it records."""
    listed = _run(["ls-files", "-z", "--others", "--exclude-standard"], root, check=False)
    # A trailing slash is a nested repository, which git lists and will not add.
    names = [name for name in listed.split("\0") if name and not name.endswith("/")]
    if len(names) > MAX_UNTRACKED_FILES or _bytes_in(root, names) > MAX_UNTRACKED_BYTES:
        return []
    return names


def _tree_of(root: Path, names: list[str], env: dict[str, str]) -> str:
    """A tree object holding exactly these files as they are on disk, built in an index of
    its own. "" when git would not build it — which `commit-tree` then refuses."""
    with tempfile.TemporaryDirectory() as scratch:
        own = {**env, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        added = subprocess.run(
            ["git", "update-index", "--add", "-z", "--stdin"], input="\0".join(names),
            cwd=str(root), env=own, capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
        return _object(root, ["write-tree"], own) if added.returncode == 0 else ""


def _bytes_in(root: Path, names: list[str]) -> int:
    """The size on disk of these paths under `root`, counting what cannot be read as nothing."""
    total = 0
    for name in names:
        try:
            total += (root / name).lstat().st_size
        except OSError:
            continue
    return total


def _object(root: Path, args: list[str], env: dict[str, str]) -> str:
    """The object name one object-writing git command prints, or "" when it failed."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), env=env, capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


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


# What "the trunk" resolves to, in the order a clone is likely to have it. `origin/HEAD`
# first because it is the only one that is right in a repository whose default branch is
# neither `main` nor `master`.
TRUNK_REFS = ("origin/HEAD", "origin/main", "origin/master")


def trunk_ref(ctx: GitContext) -> str:
    """The first trunk ref this clone actually has, or "" for a clone with no remote.

    Asked here rather than by each caller looping over the three names: a loop that treats
    "this ref does not exist" and "there is nothing to report" as the same answer stops at
    the first name and reports nothing — which is how the branch-naming line found no
    branches in a repository that had one (caught by its own test).
    """
    for ref in TRUNK_REFS:
        if _run(["rev-parse", "--verify", "--quiet", ref], ctx.worktree_root, check=False).strip():
            return ref
    return ""


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
