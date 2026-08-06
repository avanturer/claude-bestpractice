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


def worktree_refusal(ctx: GitContext, task: str = "", session_id: str = "") -> str:
    """Refuse the write, having already done the thing that resolves it.

    This used to hand the agent `git worktree add …` to run, and a command the agent runs
    is a question the founder gets asked — as a permission prompt, or as the agent stopping
    to ask whether it should. Reported as exactly that: a chip in the chat asking whether to
    use a worktree.

    Creating a worktree is not a decision the founder owns. This plugin's own autonomy line
    says to ask them for money, legal exposure and product direction; this is the plugin's
    own rule being satisfied. A hook runs without a permission prompt, so the plugin does it
    and says where to go. The last line is there because the measured failure was the agent
    being polite rather than the agent being unable.
    """
    from . import worktree

    ready = worktree.provision(ctx, task, session_id)
    if ready is None:
        # Falling back to naming the command is where this started, and it is still better
        # than a fail-closed gate crashing over a convenience.
        return (
            "claude-bestpractice: this is the main checkout, not a worktree. Several sessions "
            "sharing one working tree overwrite each other silently — git does not notice, "
            "and neither will you.\n"
            f"  {worktree_advice(ctx, task)}\n"
            "then work there, and do not ask the founder about it — run it yourself. Set "
            "`require_worktree: false` in .claude/claude-bestpractice/config.json if this "
            "repository is genuinely single-session."
        )
    return (
        "claude-bestpractice: this is the main checkout, not a worktree. Several sessions "
        "sharing one working tree overwrite each other silently — git does not notice, and "
        "neither will you.\n"
        f"  A worktree has been created for you at {ready} — `cd {ready}` and redo this "
        "write there.\n"
        "  This is not a question for the founder: do not ask whether to use a worktree, "
        "just move. Set `require_worktree: false` in "
        ".claude/claude-bestpractice/config.json if this repository is genuinely single-session."
    )


def violations(ctx: GitContext, task: str = "", session_id: str = "") -> list[str]:
    """Refusals for this write, each with the command that resolves it.

    Empty on a repository with no commits: the first session in a fresh project has
    nowhere to branch from, and refusing it would make the plugin impossible to adopt.
    """
    if not has_history(ctx):
        return []

    out: list[str] = []
    if not ctx.is_worktree:
        out.append(worktree_refusal(ctx, task, session_id))
    if on_trunk(ctx):
        out.append(
            f"claude-bestpractice: {ctx.branch} is the trunk. Work on a branch so it can be "
            "reverted and merged as one unit.\n"
            f"  git switch -c feat/{'-'.join(task.lower().split()[:4]) or 'work'}\n"
            "Set `protect_trunk: false` in .claude/claude-bestpractice/config.json to allow it."
        )
    return out


def working_trees(ctx: GitContext) -> list[Path]:
    """Every working tree sharing this repository's object store, main checkout first."""
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                out.append(Path(line.split(" ", 1)[1]).resolve())
            except OSError:
                continue
    return out


def foreign_tree(ctx: GitContext, target: Path, session_id: str = "") -> Path | None:
    """The working tree that owns `target`, when that tree is not this session's.

    The rule this file opens with — one session per working tree — was enforced by asking
    where the SESSION sat, never where the write landed. So it held in exactly one
    direction. A session in the main checkout was refused, correctly; a session in a
    worktree could write into the main checkout, or into a sibling session's tree, and
    nothing said a word. That is the failure the refusal text describes, committed by the
    gate that prints it: "one session's edit vanishing under another's, with neither
    told."

    Reported from a real machine. Leases cover part of it, but only for a file some other
    session is holding at that moment; an unheld file went straight through.

    Cheap first: a target inside our own tree is a path comparison and asks git nothing,
    which is the overwhelmingly common case on a hook that runs on every tool call.

    Two things it deliberately does NOT refuse, both reported from real machines:

    A tree nobody is standing in. The condition worth defending is "another session would
    lose work", and that is a claim about LIVE SESSIONS, not about tree identity — the
    plugin has the session list loaded for the board, each record carrying its `worktree`.
    The sweep already reasons this way when it removes unused trees; a tree safe to delete
    is a tree safe to write in. Before this, a hand-made worktree was a permanent stranger,
    because the registry only records trees the plugin provisioned, and the project's own
    convention tells people to make them by hand (#67).

    A path git cannot carry. The remedy this refusal names — make it in your own tree and
    merge it — does not exist for a file git never tracks: it is absent from the other tree
    and no merge can move a change to it. Both exits led back to each other, and a session
    that had just rotated a production SSH key could not delete the retired one (#68).
    """
    if _within(target, ctx.worktree_root):
        return None
    for tree in working_trees(ctx):
        if tree == ctx.worktree_root.resolve() or not _within(target, tree):
            continue
        if not _occupied(ctx, tree, session_id):
            return None
        if ignored_by_git(tree, target):
            return None
        return tree
    return None


def _occupied(ctx: GitContext, tree: Path, session_id: str = "") -> bool:
    """Is `tree` one somebody would lose work in?

    The main checkout always is, whoever is or is not standing in it. Under this gate no
    session is SUPPOSED to be in it, so occupancy would exempt it permanently — and its
    tracked files belong to every branch rather than to whoever happens to be there. The
    liveness question is about sibling worktrees, which exist because a session is working
    in them; #68's case in the main checkout is the uncarryable path, handled separately.

    Fails CLOSED. If the registry cannot be read the tree is treated as occupied, because
    the cost of being wrong runs one way: a refusal is an inconvenience, and a silent
    cross-tree overwrite is the thing this gate exists to prevent.
    """
    from . import sessions

    mine = sessions.my_pid(ctx, session_id)
    try:
        if tree == ctx.common_dir.parent.resolve():
            return True
    except OSError:
        return True

    try:
        live = sessions.live_sessions(ctx)
    except Exception:  # noqa: BLE001 - an unreadable registry is not permission to write
        return True
    for record in live:
        # Not me under an older identity. A resumed chat gets a NEW session id, so the
        # record it left in its previous tree is live and is not excluded by id — and the
        # session was refused its own former worktree, by a refusal telling it to go ask
        # the owner, who was the reader (#89). The process is what survives a resume.
        if mine and record.pid == mine:
            continue
        try:
            if Path(record.worktree).resolve() == tree:
                return True
        except OSError:
            return True
    return False


def ignored_by_git(tree: Path, target: Path) -> bool:
    """Is `target` a path no other working tree could ever hold?

    Two shapes, and the line between them is whether a commit could ever carry the change.

    IGNORED, wherever it is. Git is told never to track it, so it exists in exactly one
    checkout and there is no tree to make the change in (#68).

    Or PRESENT AND UNTRACKED. The same dead end arrived at from the other side: the file
    is here, no commit holds it, so nothing can carry its deletion across. The plugin's own
    attempt ledger is exactly this — untracked files in the main checkout that an agent
    could not clean up, offered a worktree that could not hold them (#92).

    A path that does not exist yet is NOT this. Creating a file is carryable in the
    ordinary way — write it in your own tree, commit, merge — and refusing that is the
    whole point of the guard. Getting this loose would open every cross-tree write.

    Asked of the OWNING tree, since that is the checkout the file lives in and whose index
    and ignore rules decide.
    """
    try:
        relative = target.resolve().relative_to(tree)
    except (ValueError, OSError):
        return False
    from .gitctx import _run

    if _run(["check-ignore", "--", relative.as_posix()], tree, check=False).strip():
        return True
    if not target.exists():
        return False
    return not _run(
        ["ls-files", "--error-unmatch", "--", relative.as_posix()], tree, check=False
    ).strip()


def owned_by_session(ctx: GitContext, target: Path) -> bool:
    """Does this write land in the tree this session is working in?"""
    return _within(target, ctx.worktree_root)


def _within(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def foreign_refusal(target: Path, owner: Path, ctx: GitContext) -> str:
    kind = "the main checkout" if owner == ctx.common_dir.parent.resolve() else "another session's worktree"
    return (
        f"claude-bestpractice: {target} belongs to {kind} ({owner}), not to this session's "
        f"working tree ({ctx.worktree_root}).\n"
        "  Editing across working trees is the exact silent overwrite worktrees exist to "
        "prevent — git does not notice, and neither would you.\n"
        "  Make the change in your own tree and merge it, or start a session there."
    )


def _provisioned_trees(ctx: GitContext, session_id: str) -> list[Path]:
    """Every working tree this plugin made for THIS session."""
    if not session_id:
        return []
    from . import store

    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return []
    out: list[Path] = []
    for path in records:
        body = store.read_json(path, default={}) or {}
        if not body.get("provisioned_by_plugin") or body.get("session_id") != session_id:
            continue
        try:
            out.append(Path(str(body.get("path") or "")).resolve())
        except OSError:
            continue
    return out


def provisioned_for(ctx: GitContext, session_id: str, target: Path) -> bool:
    """Did this plugin create `target` for THIS session?

    The refusal that hands over a worktree also made it un-removable: `git worktree remove`
    from the main checkout came back as "operates on another session's worktree", although
    the hook had created it for this very session seconds earlier — and a worktree cannot
    remove itself from the inside either. Every false-positive refusal therefore left
    permanent litter that only a terminal could clear. Reported as issue #37.

    Narrow on purpose: a tree this plugin made, for the session asking, and nothing else. A
    sibling session's tree stays refused whoever provisioned it.
    """
    try:
        resolved = target.resolve()
    except OSError:
        return False
    return resolved in _provisioned_trees(ctx, session_id)


def provisioned_tree_of(ctx: GitContext, session_id: str, target: Path) -> Path | None:
    """The tree this plugin made for this session that `target` lives inside, if any.

    Containment rather than equality, because the question this answers is about a FILE
    and the record names a directory. Kept beside `provisioned_for` so the two cannot come
    to disagree about which trees are ours — one reader of that fact was corrected without
    the other twice now (#89, #100), and this is the third place that asks.
    """
    try:
        resolved = target.resolve()
    except OSError:
        return None
    for tree in _provisioned_trees(ctx, session_id):
        if resolved == tree or tree in resolved.parents:
            return tree
    return None


def foreign_git_refusal(owner: Path, ctx: GitContext) -> str:
    """A git command aimed at somebody else's working tree.

    Separate from the file refusal because the loss is a different shape and the founder
    should be told which one happened: no path is named, nothing appears in a diff, and
    `reset --hard` or `clean -fd` takes uncommitted work that was never written anywhere
    else. Every rule keyed on "which files does this write" saw nothing at all here.
    """
    kind = "the main checkout" if owner == ctx.common_dir.parent.resolve() else "another session's worktree"
    return (
        f"claude-bestpractice: this git command operates on {kind} ({owner}), not on this "
        f"session's working tree ({ctx.worktree_root}).\n"
        "  reset, checkout, switch, clean and stash discard uncommitted work and move the "
        "HEAD another session is standing on. Nothing names a file, so nothing shows up in "
        "a diff and no lease covers it.\n"
        "  Run it in your own tree, or let the session that owns that one run it."
    )


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
