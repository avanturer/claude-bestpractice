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

import os
import re
import shlex
import subprocess
from pathlib import Path

from . import config, shellcmd
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
    return f"git worktree add -b feat/{slug} {shlex.quote(str(target))}"


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

    Every path put into a command here is quoted for the shell. Unquoted, a repository under
    `…/final space ü/app` was told `cd …/final space ü/app/.claude/worktrees/…`, which bash
    answers with "too many arguments" — a refusal whose one way out does not run.
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
            "then work there, and do not ask the founder about it — run it yourself.\n"
            "  If this repository is genuinely single-session: "
            + config.switch_advice("require_worktree", False)
        )
    return (
        "claude-bestpractice: this is the main checkout, not a worktree. Several sessions "
        "sharing one working tree overwrite each other silently — git does not notice, and "
        "neither will you.\n"
        f"  A worktree has been created for you at {ready} — `cd {shlex.quote(str(ready))}` "
        "and redo this write there.\n"
        "  This is not a question for the founder: do not ask whether to use a worktree, "
        "just move.\n"
        "  If this repository is genuinely single-session: "
        + config.switch_advice("require_worktree", False)
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
        # Quoted: the name is the founder's words, and `fix the user's login` put an
        # unterminated quote into the one command this refusal offers.
        branch = f"feat/{'-'.join(task.lower().split()[:4]) or 'work'}"
        out.append(
            f"claude-bestpractice: {ctx.branch} is the trunk. Work on a branch so it can be "
            "reverted and merged as one unit.\n"
            f"  git switch -c {shlex.quote(branch)}\n"
            + config.switch_advice("protect_trunk", False)
        )
    return out


def _final_directory(command: str, cwd: Path) -> Path | None:
    """Where the shell would be standing after this line. None when it cannot be told.

    Only top-level `cd`s count. A subshell — `(cd /repo && git log)` — leaves the parent
    exactly where it was, and `cd -` goes somewhere only the shell knows; both return
    without a verdict rather than with a guess, because the caller REFUSES on this answer.
    """
    here = cwd
    for argv in shellcmd.commands(command):
        # A subshell arrives as `['(', 'cd', '/repo']`, so the opening bracket is what
        # argv[0] holds and the test below skips it — which is the wanted answer, because
        # `(cd /repo && …)` leaves the parent shell exactly where it was. Written down
        # because a guard for it was tried here and no mutation could kill it: the check
        # that follows was already doing the work.
        if not argv or argv[0] != "cd":
            continue
        # `-` and every other flag drop out here, which also disposes of `cd -`: the
        # shell knows where it was last and this does not, so an empty target is the
        # honest answer and the caller refuses nothing on it.
        target = [token for token in argv[1:] if not token.startswith("-")]
        if not target:
            return None
        here = Path(target[0]) if os.path.isabs(target[0]) else here / target[0]
    return here


def strands_the_shell(command: str, cwd: Path, ctx: GitContext) -> Path | None:
    """The directory this command would leave the shell in, when that is a trap.

    Claude Code isolates a session to its worktree by refusing any Bash call whose
    WORKING DIRECTORY is inside the protected checkout — judged before the command runs,
    so it refuses `cd back` as readily as anything else, and the persistent shell is then
    unusable for the rest of the session. Read out of the CLI binary rather than inferred:

        `${noun} is isolated in the worktree ${t}, but this command's working directory
         resolved to the shared checkout (${e}). Refusing to run it there …`

    with no setting that turns it off for an isolated session (the `worktree.bgIsolation`
    escape hatch covers only background subagents). The founder reported the dead end
    against four commands, `env -C <worktree>` among them, which is what proves the guard
    reads the shell's cwd rather than the command's target (#174).

    So this refuses the STEP INTO it. One turn's friction against a shell that cannot be
    recovered without restarting the session — and the step was already against this
    plugin's own rule, which is why it costs nothing to refuse.
    """
    from . import worktree

    if not ctx.is_worktree:
        return None
    landing = _final_directory(command, cwd)
    if landing is None:
        return None
    try:
        here = landing.resolve()
        if here == ctx.worktree_root.resolve() or ctx.worktree_root.resolve() in here.parents:
            return None
        guarded = worktree.main_checkout(ctx).resolve()
    except OSError:
        return None
    return landing if here == guarded or guarded in here.parents else None


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
    if _ours(ctx, target):
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

    if git_ignores(tree, relative.as_posix()):
        return True
    # A name the filesystem refuses to look up — longer than it allows, or under a
    # directory it may not read — raises from `exists()` rather than answering, and this
    # runs inside a gate that fails closed: `echo x > <300 characters>.txt` was a crash.
    # Such a file is not present, which is all this line is asking.
    try:
        present = target.exists()
    except OSError:
        present = False
    if not present:
        return False
    return not _run(
        ["ls-files", "--error-unmatch", "--", relative.as_posix()], tree, check=False
    ).strip()


def git_ignores(tree: Path, relative: str) -> bool:
    """Does git in `tree` ignore this path, whether or not it exists yet?

    Asked a second time with a trailing slash when nothing is there. A rule written for a
    directory, `build/`, matches only what git can see is a directory, and one that does
    not exist yet is not — so `rm -rf build`, with `build/` ignored and absent, was read as
    a write to a path a commit would carry: refused in the main checkout, and a worktree
    provisioned to hold nothing.
    """
    from .gitctx import _run

    asked = [relative]
    try:
        absent = not (tree / relative).exists()
    except OSError:
        absent = True
    if absent:
        asked.append(relative.rstrip("/") + "/")
    return bool(_run(["check-ignore", "--", *asked], tree, check=False).strip())


def owned_by_session(ctx: GitContext, target: Path) -> bool:
    """Does this write land in the tree this session is working in?"""
    return _ours(ctx, target)


def _ours(ctx: GitContext, target: Path) -> bool:
    """Inside this session's tree, and not inside a working tree nested within it.

    Plain containment stopped being the whole answer when provisioned trees moved under
    `.claude/worktrees/` (#111): the main checkout then CONTAINS every other session's
    tree, so a path comparison alone hands all of them to whoever is standing in the main
    checkout — the silent cross-tree overwrite this file exists to prevent, reintroduced
    by a change made two files away. Caught by a test that had been passing for eleven
    versions, which is the argument for having written it.

    Path arithmetic and not `git worktree list`, because this runs on every tool call and
    the boundary is a location this plugin chooses rather than one it has to discover.
    """
    if not _within(target, ctx.worktree_root):
        return False
    from .worktree import HOME

    return not _within(target, ctx.worktree_root / HOME)


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
    """Every working tree this plugin made for THIS session, under any id it has had.

    The tree is recorded for the id the session had when it was refused in the main
    checkout, and the session is a new id wherever else it stands (`sessions.identities`).
    `worktree.mine` answers the same question the same way: two readers of this one fact
    that disagree are how #89 and #100 happened.
    """
    if not session_id:
        return []
    from . import sessions, store

    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return []
    mine = sessions.identities(ctx, session_id)
    out: list[Path] = []
    for path in records:
        body = store.read_json(path, default={}) or {}
        if not body.get("provisioned_by_plugin") or body.get("session_id") not in mine:
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

    Nobody owns the main checkout, so for it the second half of that advice named nobody —
    and it is the tree decision 0018 needs kept level with the trunk. It is told the one
    update that is allowed there instead.
    """
    main = owner == ctx.common_dir.parent.resolve()
    kind = "the main checkout" if main else "another session's worktree"
    return (
        f"claude-bestpractice: this git command operates on {kind} ({owner}), not on this "
        f"session's working tree ({ctx.worktree_root}).\n"
        "  reset, checkout, switch, clean and stash discard uncommitted work and move the "
        "HEAD another session is standing on. Nothing names a file, so nothing shows up in "
        "a diff and no lease covers it.\n"
        + (
            "  Run it in your own tree. Bringing the main checkout up to the trunk is allowed, "
            "and so is anything that only reads it:\n"
            f"  git -C {shlex.quote(str(owner))} pull --ff-only"
            if main else
            "  Run it in your own tree, or let the session that owns that one run it."
        )
    )


def split_git(argv: list[str]) -> tuple[list[str], str, list[str]]:
    """A git command as the trees `-C` and `--work-tree` point it at, its subcommand, and
    that subcommand's own arguments. All three empty for anything that is not git.

    From the words the shell hands git, so a quoted `-C "<a path with a space>"` is the
    path: read off the text with quoted spans blanked, it came back as whatever word
    followed, and the command was judged in the session's own tree instead.
    """
    if not argv or argv[0].rsplit("/", 1)[-1] != "git":
        return [], "", []
    pointed: list[str] = []
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        word = argv[index]
        if word in ("-C", "--work-tree") and index + 1 < len(argv):
            pointed.append(argv[index + 1])
        elif word.startswith("--work-tree="):
            pointed.append(word.split("=", 1)[1])
        index += 2 if word in _GLOBAL_WITH_VALUE else 1
    subcommand = argv[index] if index < len(argv) else ""
    return pointed, subcommand, argv[index + 1:]


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

# The same flag with the message handed over by a heredoc — `-m "$(cat <<'EOF' … EOF)"`,
# which is how Claude Code writes every multi-line commit. Anchored on the terminator, so a
# quote inside the message cannot end it early the way it ends the pattern above.
_HEREDOC_MESSAGE = re.compile(
    r"""git(?:\s+-[cC]\s+\S+|\s+--\S+)*\s+commit\b[^\n]*?(?:-[a-zA-Z]*m|--message=?)\s*"\$\(\s*cat\s*<<-?\s*"""
    r"""(?P<q>['"]?)(?P<tag>\w+)(?P=q)[ \t]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*\n\s*\)""",
    re.S,
)

# What the shell rewrites inside double quotes before git ever sees the message.
_EXPANDED = re.compile(r"\$[({\w]|`")

# Any other heredoc is data handed to a command — a script being written, a query being
# run — and a `git commit` inside one is not being run. Its body was tokenised with the rest
# of the line, so `cat > release.sh <<'EOF'` over a body holding `git add -A && git commit
# -m "Release"` split out a commit of "Release" and refused it as seven characters.
_HEREDOC_DATA = re.compile(r"<<-?\s*(['\"]?)(?P<tag>\w+)\1.*?^\s*(?P=tag)\s*$", re.S | re.M)

# `=======` alone is also how Markdown and reStructuredText underline a seven-character
# heading, so requiring the OPENING marker as well is what stops `Options` under a row of
# equals signs being hard-refused as an unresolved conflict.
CONFLICT_MARKERS = re.compile(r"(?m)^<{7}(?:\s|$).*?^={7}(?:\s|$)", re.S)


# `git commit`, in every spelling that reaches a shell: with global options before the
# subcommand (`git -C x commit`), with any flags after it, with or without a message on
# the line. `commit_message` cannot answer this — `git commit --amend --no-edit` carries
# no message and is still a commit.
COMMITS = re.compile(r"\bgit\s+(?:(?:-[cC]\s+\S+|--[a-z-]+(?:=\S+)?|-\w+\s+\S+)\s+)*commit\b")


def commits(command: str) -> bool:
    """Is this command line a `git commit`?"""
    return bool(COMMITS.search(command or ""))


# Work done entirely through git, which no write gate can see. Every rule asking "is this
# session doing something the board should be saying" reads the paths a call WRITES, and
# these name none: `git merge`, `git rebase`, `git cherry-pick`, `git revert` and
# `git am`/`git apply` rewrite the tree and the history without one Write call between
# them. So a session could take somebody else's branch in, revert a release, or replay a
# patch series with nothing on the board at all — which is the state the board exists not
# to be in.
#
# `status`, `log`, `diff` and `show` are absent on purpose, and so is `commit`: the first
# four are reconnaissance, and by the time anything is committed the write demand has
# already fired. `push` is absent for a sharper reason — a merge this plugin gates closes
# the cards it delivered, so demanding a card for the push that follows would refuse a
# session for having just been closed correctly.
CHANGES_THE_REPOSITORY = ("merge", "rebase", "cherry-pick", "revert", "am", "apply")

# The end of an operation already in flight, never the start of one. Refusing these would
# strand a session in a conflicted tree it is then not allowed to leave — the wedge every
# rule in this file is written against — and `--check` and the `apply` report flags write
# nothing at all.
_NOT_STARTING_WORK = {"--abort", "--quit", "--skip", "--continue", "--check",
                      "--stat", "--numstat", "--summary"}


def changes_the_repository(command: str) -> str:
    """The git subcommand in this line that rewrites the tree or history, or "".

    Decided on the PROGRAM being run rather than on the text, for the reason #76 records:
    `echo "git merge main"` is not a merge, and a gate that matched the string refused the
    very tool being used to investigate it. A line the tokeniser cannot read returns
    nothing and therefore allows — the opposite direction from the refusal gates, and
    deliberate, because this one is asked before ordinary work rather than before
    something irreversible.
    """
    for argv in shellcmd.commands(command or ""):
        if argv[0].rsplit("/", 1)[-1] != "git":
            continue
        verb, rest = _subcommand_of(argv)
        if verb not in CHANGES_THE_REPOSITORY or _NOT_STARTING_WORK.intersection(rest):
            continue
        if verb in ("merge", "rebase") and any(_names_the_trunk(arg) for arg in rest):
            continue
        return verb
    return ""


def _names_the_trunk(token: str) -> bool:
    """Is this argument the base branch, with or without a remote in front of it?

    Taking the base branch in is not the start of work — it is the maintenance step this
    plugin's own pull-request flow ORDERS, and a gate that refuses the command satisfying
    it is the trap every rule in this file is written to avoid. So `git merge origin/main`
    and `git rebase main` stay free while `git merge feat/theirs` does not: the second is
    somebody's work arriving, which is the thing a board is for.

    A branch genuinely named `feat/main` is read as the trunk here. That errs towards
    allowing a call with no card, which is where this rule stood yesterday, and the
    alternative needs the repository's own default branch in a function that is otherwise
    pure text.
    """
    if token.startswith("-"):
        return False
    head, _, tail = token.rpartition("/")
    return token in TRUNK_NAMES or (bool(tail in TRUNK_NAMES) and "/" not in head)


# Global options come BEFORE the subcommand, and two of them take a value. `shellcmd.runs`
# compares the words straight after the program, so `git -C ../other merge main` read as a
# call to a subcommand named `-C` and walked past every rule keyed on the verb. `COMMITS`
# above already carries its own spelling of this; the argv form is the one that does not
# need a new regex the next time a rule wants a different verb.
_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                      "--exec-path", "--config-env"}


def _subcommand_of(argv: list[str]) -> tuple[str, list[str]]:
    """A git argv split into its subcommand and that subcommand's own arguments."""
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] in _GLOBAL_WITH_VALUE else 1
    return (argv[index], argv[index + 1:]) if index < len(argv) else ("", [])


# Pathspecs that mean "whatever is in the tree" rather than "what I worked on". `-u`
# belongs here too: it updates every tracked file anywhere in the checkout, which is the
# same accident with a narrower blast radius.
_EVERYTHING = {"-A", "--all", "--no-ignore-removal", "-u", "--update", "."}


def stages_everything(command: str) -> bool:
    """Does this line stage the whole tree instead of the paths the session names?

    Decided on the PROGRAM and its subcommand, never on the text: `echo "git add -A"` is a
    sentence about staging, and a gate that matched the string refused the tool being used
    to investigate it (#76).
    """
    from . import shellcmd

    return any(_stages_all(argv) for argv in shellcmd.acting(command or ""))


def _stages_all(argv: list[str]) -> bool:
    """Does this one command stage the whole tree? False for anything that is not git."""
    if not argv or argv[0].rsplit("/", 1)[-1] != "git":
        return False
    sub, args = _subcommand_of(argv)
    if sub == "add":
        return any(arg in _EVERYTHING for arg in args)
    return sub == "commit" and any(_is_commit_all(arg) for arg in args)


def _is_commit_all(arg: str) -> bool:
    """`git commit -a`, `--all`, or an `a` bundled into a short group like `-am`."""
    if arg == "--all":
        return True
    return arg.startswith("-") and not arg.startswith("--") and "a" in arg[1:]


# The ledger, whoever wrote it. Its files belong to every session in the clone and to none
# of them, which is why they are out of git since v1.65.0 — and why a clone still carrying
# them tracked must not have them swept into somebody's feature commit.
_LEDGER_PREFIX = ".claude/claude-bestpractice/plan/"


def whose_work_is_in_the_way(ctx: GitContext, session_id: str) -> tuple[list[str], list[str]]:
    """Dirty paths in this tree that are NOT this session's, and the ones that are.

    The accident this exists to stop was caught by hand, on a hair, and reported as it
    happened: "при обычном `git add -A` в коммит затянуло ~50 файлов
    .claude/claude-bestpractice/plan/ от чужих веток — пришлось делать reset --soft"
    (#220). A shared checkout makes that the DEFAULT outcome of the commonest staging
    command there is.

    Somebody else's is read from what the coordination layer already knows — a lease a live
    sibling holds, a path a live sibling's card names — plus the ledger, which belongs to
    everybody. Everything else is this session's, because a gate that has to guess whose a
    file is should be deciding in the session's favour.
    """
    dirty = _dirty_paths(ctx)
    if not dirty:
        return [], []
    try:
        claimed = _claimed_here(ctx, session_id)
    except Exception:  # noqa: BLE001 - a refusal must never come out of a crashed read
        return [], []
    foreign = [
        path for path in dirty
        if path.startswith(_LEDGER_PREFIX) or any(_under(path, claim) for claim in claimed)
    ]
    return sorted(foreign), sorted(path for path in dirty if path not in set(foreign))


def _claimed_here(ctx: GitContext, session_id: str) -> set[str]:
    """What the live sessions standing in THIS tree have leased or named — other than this one.

    This tree only, because a file of the same name in another tree is another file: two
    sessions in two trees each staging their own `README.md` is a merge later, never one
    commit carrying the other's work (#163). Counting every tree refused a session's
    `git add -A` in its own tree over a sibling's lease in the sibling's tree.

    Never this session's, under any id it has had. It files its card in the main checkout
    and then moves into its own tree, which makes it a second identity with the first one
    still live — and that first record, naming the very paths being staged, was read as a
    sibling's, refusing the ordinary commit in the tree this plugin sent it to.
    """
    from . import sessions

    here = ctx.worktree_root.resolve()
    mine = sessions.identities(ctx, session_id)
    claimed: set[str] = set()
    for record in sessions.live_sessions(ctx):
        if record.session_id in mine or Path(record.worktree).resolve() != here:
            continue
        claimed.update(sessions.leases_held_by(ctx, record.session_id, here))
        claimed.update(path for path in record.task_paths if path)
    return claimed


def _under(path: str, claim: str) -> bool:
    """Is `path` the claimed path, or inside it when the claim names a directory?"""
    claim = claim.strip().rstrip("/")
    if not claim or claim == ".":
        return False
    return path == claim or path.startswith(claim + "/")


def _dirty_paths(ctx: GitContext) -> list[str]:
    """Every path `git add -A` would pick up here, as git reports them.

    Parsed by `evidence._porcelain_path`, which already gets the two things wrong with
    slicing a fixed column: `_run` strips its output, so the first line loses the leading
    space of a ` M` status, and a rename has to be read as its destination.
    """
    from .evidence import _porcelain_path
    from .gitctx import _run

    try:
        raw = _run(
            ["-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all"],
            ctx.worktree_root, check=False,
        )
    except Exception:  # noqa: BLE001 - see above
        return []
    return [path for path in (_porcelain_path(line) for line in raw.splitlines()) if path]


def commit_message(command: str) -> str:
    """The message out of a `git commit -m` command line, or empty if there is none.

    Empty, too, when the shell writes the message rather than the command line: judging
    `"$(cat <<'EOF'"` as a thirteen-character subject refused every multi-line commit Claude
    Code makes, and a `"$MSG"` is a variable name, not a message.
    """
    heredoc = _HEREDOC_MESSAGE.search(command)
    if heredoc:
        return heredoc.group("body")
    command = _HEREDOC_DATA.sub(" ", command)
    parsed = shellcmd.commands(command)
    if not parsed:
        # Not tokenisable here, so the text is all there is to go on.
        return _message_by_pattern(command)
    # From the shell's own reading of the line, not a pattern over its text. The pattern
    # ended the message at the first quote it met: `-m "Handle \"quoted\" fields …"` was
    # judged as `Handle \`, `-m 'Don'\''t crash …'` as `Don` — refusals no rewording could
    # satisfy — and a `git commit` inside a heredoc being written to a script was judged
    # as if it were being run.
    for argv in parsed:
        if argv[0].rsplit("/", 1)[-1] != "git":
            continue
        # Past git's own options: `git -C <tree> commit -m …` is how a session commits in
        # its tree from anywhere, and reading only `argv[1]` let every such message through.
        verb, args = _subcommand_of(argv)
        if verb == "commit":
            message = _message_argument(args)
            if message is not None:
                return "" if _EXPANDED.search(message) else message
    return ""


def _message_by_pattern(command: str) -> str:
    match = COMMIT_MESSAGE.search(command)
    if not match or (match.group("q") == '"' and _EXPANDED.search(match.group("message"))):
        return ""
    return match.group("message")


# `git commit` flags that take no value, so `-am`, `-qm` and `-vm` are still the message flag.
_VALUELESS = set("aeinqsvz")


def _message_argument(args: list[str]) -> str | None:
    """The first `-m`/`--message` value among `git commit`'s arguments, None for none."""
    for index, arg in enumerate(args):
        if arg == "--":
            return None
        if arg.startswith("--message="):
            return arg.split("=", 1)[1]
        glued = _glued_message(arg)
        if glued:
            return glued
        if arg == "--message" or glued == "":
            return args[index + 1] if index + 1 < len(args) else ""
    return None


def _glued_message(arg: str) -> str | None:
    """For a short-flag cluster that ends in the message flag: what is glued after the `m`.

    `-m` and `-am` give "" (the message is the next argument), `-mwip` gives "wip", and
    anything that is not such a cluster gives None.
    """
    if not arg.startswith("-") or arg.startswith("--") or "m" not in arg:
        return None
    before, _, after = arg[1:].partition("m")
    return after if set(before) <= _VALUELESS else None


# How many recent subjects decide whether this repository has a convention, and how many
# of them must carry it. A majority rather than any, so one `fix: …` in a hundred plain
# subjects does not impose a convention on a repository that plainly has none.
_HISTORY_READ = 20
_CONVENTION_SHARE = 0.5


def uses_conventional_commits(ctx: GitContext) -> bool:
    """Does this repository's own history use conventional commits?

    Asked because the gate was imposing one on repositories that had never had one: a
    scratch repository with a single commit was refused `git commit -m "add the parser"`
    over a convention nobody in it had ever used and the founder had not asked for (#215).

    A convention is a fact about a project, and this repository writes it down in the one
    place that cannot be wrong about it — its own log. An empty or unreadable history reads
    as "no convention", which is the direction that refuses less.
    """
    from .gitctx import _run

    try:
        listing = _run(
            ["log", f"-{_HISTORY_READ}", "--no-merges", "--format=%s"],
            ctx.worktree_root, check=False,
        )
    except Exception:  # noqa: BLE001 - an unreadable log is not a convention
        return False
    subjects = [line.strip() for line in listing.splitlines() if line.strip()]
    if not subjects:
        return False
    carried = sum(1 for subject in subjects if CONVENTIONAL.match(subject))
    return carried >= max(1, int(len(subjects) * _CONVENTION_SHARE))


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
