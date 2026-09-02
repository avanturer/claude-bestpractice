"""Calls this plugin ORDERED or already governs, decided by parse rather than by string.

`allow_tool` ends the permission pipeline, so nothing here may run before the gates: every
function is consulted at the very end of `pre-tool`, after every refusal has had its say. A
vouch is the last word on a call the plugin was going to allow anyway — never a way past
one of its own rules.

Measured on one machine over three days: 35 classifier prompts, three of which were worth
asking about (ssh to production, a catalogue push, a merge). The other 32 were reading,
this project's own checks, and writes inside the tree the session was standing in. v1.11.0
vouched for three literal strings, so `make test` passed and `ruff check src/` did not, and
the founder went back to hand-writing prose into `autoMode.allow` — prose describing which
tree the session owns and what this project's checks are, both of which the plugin computes
on every hook call. This module answers by predicate instead (#102).

Prefix rules cannot see inside `cd backend && ruff check && pytest -q`; the classifier can
parse it but does not know which tree this session owns. The plugin is the only layer with
both, which is the argument that produced `allow_tool` in the first place.

Three rules keep a predicate from becoming a bypass:

- **Whitelist, never blacklist.** A program not named here is not vouched for. Production
  is therefore out by construction rather than by a list of things to remember, and every
  new attack surface arrives silent rather than approved.
- **The line, whole.** `segments`, not `commands`: no env assignment stripped, no wrapper
  unwrapped, no `$(…)` or backtick, no redirection. Anything this cannot account for ends
  the vouch for the entire line.
- **Every segment, independently.** One unqualified segment takes the line with it, because
  `allow_tool` approves the line and there is no half of it to approve.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import shellcmd
from .gitctx import GitContext

WORKTREE = (
    "claude-bestpractice: this gate orders a worktree and names `git worktree` for making "
    "one, so the CLI spelling of the move needs no further permission — `EnterWorktree` "
    "already has none."
)
SUITE = (
    "claude-bestpractice: the evidence gate refuses a finish without a run of this "
    "project's checks. Demanding a suite and then interrogating the suite is the plugin "
    "arguing with itself."
)
READ = (
    "claude-bestpractice: this reads inside the repository and writes nothing — no target, "
    "no redirection, no substitution. Nothing it can do needs authorising."
)
WRITE = (
    "claude-bestpractice: this is the working tree this session occupies. Refusing writes "
    "into every OTHER tree is this plugin's own rule; the same answer, inverted, is a "
    "vouch rather than a prompt."
)
MOVE = (
    "claude-bestpractice: this only moves around inside the repository, or does nothing at "
    "all. A session asked to work in its own worktree has to be able to walk back into it, "
    "and a rule that makes `cd` a question is a rule that strands the session it directs."
)
EXIT = (
    "claude-bestpractice: one tree per session is this gate's rule, and leaving is the last "
    "step of the convention it publishes. Approving the way in and asking about the way out "
    "is the same interruption, one call later."
)
PULL_REQUEST = (
    "claude-bestpractice: this plugin refuses a finish that leaves committed work with no "
    "pull request, so opening one is its own instruction being followed. Asking the founder "
    "whether to open it is the formality the obligation exists to remove."
)
MERGE = (
    "claude-bestpractice: this merge was judged by this gate one step ago and nothing was "
    "found standing against it. Refusing a merge with blockers and then asking about one "
    "without any leaves the founder to re-decide what was already decided."
)

# Moving around is not doing anything, and the founder's real commands are compound:
# `cd <tree> && ruff check` is the shape the measurements are full of.
_NAVIGATION = {"cd", "pwd", "pushd", "popd", "true", ":"}

_WORKTREE_VERBS = {"add", "remove", "list"}

# git subcommands that cannot change a file, an index or a ref. `tag`, `branch -d`, `stash`
# and `config` are absent because each of them writes when given the right argument, and a
# vouch that has to reason about which argument is a vouch that will one day be wrong.
_GIT_READS = {
    "log", "diff", "status", "show", "rev-parse", "ls-files", "blame", "describe",
    "shortlog", "cat-file", "diff-tree", "rev-list", "whatchanged", "grep", "ls-tree",
}

# Programs whose every invocation reads. `sed`, `awk`, `find` and `sort` are deliberately
# absent: `sed -i`, `awk '… > f'`, `find -delete` and `sort -o` all write, and the reader
# that tried to tell those apart by flag is the one that eventually approves a deletion.
_READ_ONLY = {
    "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep", "nl", "cut", "uniq",
    "column", "jq", "diff", "cmp", "file", "stat", "basename", "dirname", "realpath",
    "ls", "tree", "du", "less", "more", "echo", "printf",
}

# Checks, by family rather than by the one string the detector happened to produce.
_CHECKERS = {
    "pytest", "ruff", "mypy", "tsc", "jest", "vitest", "eslint", "flake8", "pylint",
    "phpunit", "rspec", "tox", "nox", "shellcheck", "hadolint", "black", "isort",
}
_CHECK_SUBCOMMANDS = {
    "go": {"test", "vet"},
    "cargo": {"test", "check", "clippy", "fmt"},
    "dotnet": {"test"},
    "mvn": {"test", "verify"},
    "gradle": {"test", "check"},
}
# `bundle exec rspec` is a check; `bundle exec rm -rf /` is not, and they differ only in a
# word this has to actually read. Runners that take a whole command are handled by looking
# at the command they take, never by the runner's name.
_DELEGATING = {"bundle": "exec", "uv": "run", "poetry": "run", "pipenv": "run", "npx": ""}
# `python -m pytest` is the detector's own answer for most repositories.
_CHECK_MODULES = {"pytest", "ruff", "mypy", "unittest", "tox", "flake8", "pylint", "black"}
# A script name is the project's, so this is a name the founder chose — kept to the ones
# that mean "check", because `npm run deploy` is a script too.
_CHECK_SCRIPTS = {
    "test", "tests", "lint", "typecheck", "type-check", "check", "checks", "verify",
    "unit", "ci", "fmt-check", "format-check", "audit",
}
_SCRIPT_RUNNERS = {"npm", "pnpm", "yarn", "bun"}

# What this plugin will not vouch for reading, whatever the program. The boundary names
# these explicitly rather than trusting "it is inside the repository": a credential in the
# tree is still a credential, and putting it in the transcript is the loss.
_SECRETISH = re.compile(
    r"(^|/)(\.env(\.|$)|\.secrets?(/|$)|\.aws(/|$)|\.ssh(/|$)|id_[a-z]+|\.netrc|\.npmrc|"
    r"\.pypirc|credentials(\.|$))|\.(pem|key|p12|pfx|keystore)$"
)

# Tokens that mean the shell is doing something this cannot see: a redirection, a
# substitution, an expansion. Their presence ends the vouch rather than being interpreted.
_REDIRECTS = {">", ">>", "<", ">|", "<<", "<<<", "&>", ">&", "<&"}
_INDIRECTION = re.compile(r"[$`]|\$\(|<\(")


def _accountable(argv: list[str]) -> bool:
    """Is every token of this segment something the rules below actually looked at?

    `FOO=bar cmd` and `timeout 30 cmd` need no clause of their own: `segments` leaves the
    assignment and the wrapper in place, so the program this reads is `FOO=bar` or
    `timeout`, and neither is on any whitelist. Stripping them — which is what `commands`
    does for the refusals — would have handed back the vouchable half and dropped the half
    that runs.
    """
    if not argv:
        return False
    return not any(t in _REDIRECTS or _INDIRECTION.search(t) for t in argv)


def _program(argv: list[str]) -> str:
    return argv[0].rsplit("/", 1)[-1]


def _resolve(here: Path, token: str) -> Path | None:
    """Where a path-shaped argument points, or None when it cannot be reasoned about."""
    if token.startswith("~"):
        return None
    # A glob is answered by its fixed prefix: `/etc/*` is outside the repository whether or
    # not anything matches it, and the alternative — treating an unmatched glob as a
    # harmless pattern — approves exactly that read.
    fixed = re.split(r"[*?\[]", token, maxsplit=1)[0]
    if fixed != token:
        fixed = fixed.rsplit("/", 1)[0] if "/" in fixed else "."
    try:
        return (Path(fixed) if fixed.startswith("/") else here / fixed).resolve()
    except (OSError, ValueError):
        return None


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _arguments(argv: list[str]) -> list[str]:
    return [t for t in argv[1:] if not t.startswith("-")]


def _paths_are_ours(root: Path, here: Path, argv: list[str]) -> bool:
    """Every path-shaped argument lands inside this tree, and none of them is a credential.

    An argument that is a pattern rather than a path — `grep TODO src/` — resolves under
    the segment's own directory and passes, which is correct: it names nothing outside.
    """
    for token in _arguments(argv):
        if _SECRETISH.search(token):
            return False
        resolved = _resolve(here, token)
        if resolved is None or not _inside(root, resolved):
            return False
    return True


def _git_arguments(argv: list[str]) -> list[str]:
    """`git worktree add …` → `["worktree", "add", …]`, or [] when this is not plain git.

    A global option ends it rather than being stepped over. `-c core.pager='sh -c …'` runs
    a command of the attacker's choosing through what looks like `git log`, and `-C <dir>`
    aims the whole thing at a tree this session does not own — neither is something to
    parse past on the way to approving a read.
    """
    if not argv or _program(argv) != "git":
        return []
    rest = argv[1:]
    return [] if rest and rest[0].startswith("-") else rest


def _orders_a_worktree(argv: list[str]) -> bool:
    arguments = _git_arguments(argv)
    return len(arguments) >= 2 and arguments[0] == "worktree" and arguments[1] in _WORKTREE_VERBS


def _reads(root: Path, here: Path, argv: list[str]) -> bool:
    program = _program(argv)
    if program == "git":
        arguments = _git_arguments(argv)
        if not arguments or arguments[0] not in _GIT_READS:
            return False
        # `git diff --output f` writes a file; the rest of git's read verbs have no such
        # flag, and one that grows one should not be discovered here.
        return not any(a.startswith("--output") for a in arguments) and _paths_are_ours(
            root, here, ["git", *arguments[1:]])
    return program in _READ_ONLY and _paths_are_ours(root, here, argv)


def _module_check(argv: list[str]) -> bool:
    """`python -m pytest -q`, and only with `-m`: `python script.py` runs anything."""
    if _program(argv) not in ("python", "python3", "py"):
        return False
    return len(argv) >= 3 and argv[1] == "-m" and argv[2] in _CHECK_MODULES


def _make_check(argv: list[str], test_command: list[str]) -> bool:
    """`make test` — the targets that mean "check", plus whatever this project detected."""
    if _program(argv) != "make":
        return False
    targets = _arguments(argv)
    detected = set(test_command[1:]) if test_command[:1] == ["make"] else set()
    return bool(targets) and all(t in _CHECK_SCRIPTS or t in detected for t in targets)


def _script_check(argv: list[str]) -> bool:
    """`npm test`, `pnpm run lint` — a script the project named, from the check family."""
    if _program(argv) not in _SCRIPT_RUNNERS:
        return False
    rest = [a for a in argv[1:] if not a.startswith("-")]
    if rest[:1] == ["run"]:
        rest = rest[1:]
    return len(rest) == 1 and rest[0] in _CHECK_SCRIPTS


def _subcommand_check(argv: list[str]) -> bool:
    """`go test ./...`, `cargo clippy`, `mvn verify` — a check named by its subcommand.

    `go test github.com/x/y` fetches somebody else's code over the network, so a target
    that is not a local path is the far side of the boundary rather than this project.
    """
    arguments = _arguments(argv)
    if not arguments or arguments[0] not in _CHECK_SUBCOMMANDS[_program(argv)]:
        return False
    return all(a.startswith((".", "/")) for a in arguments[1:])


def _syntax_check(argv: list[str]) -> bool:
    """`bash -n file` parses without running — the one shape of `bash` that is a check."""
    return _program(argv) in ("bash", "sh") and "-n" in argv


def _checks(root: Path, here: Path, argv: list[str], test_command: list[str]) -> bool:
    if argv == list(test_command):
        return True
    program = _program(argv)
    if program in _DELEGATING:
        return _delegated(root, here, argv, test_command)
    named = (
        program in _CHECKERS
        or _module_check(argv)
        or _script_check(argv)
        or (program in _CHECK_SUBCOMMANDS and _subcommand_check(argv))
        or _syntax_check(argv)
    )
    if not named:
        return _make_check(argv, test_command)
    return _paths_are_ours(root, here, argv)


def _delegated(root: Path, here: Path, argv: list[str], test_command: list[str]) -> bool:
    """`bundle exec rspec` — judged by what it runs, not by the runner's name."""
    keyword = _DELEGATING[_program(argv)]
    rest = argv[1:]
    if keyword:
        if rest[:1] != [keyword]:
            return False
        rest = rest[1:]
    return bool(rest) and _checks(root, here, rest, test_command)


def _commits_here(root: Path, here: Path, argv: list[str]) -> bool:
    """`git add` / `git commit` in the tree this session is standing in."""
    arguments = _git_arguments(argv)
    if not arguments or arguments[0] not in ("add", "commit"):
        return False
    # `--no-verify` skips the pre-commit hook. A plugin whose central claim is that a
    # finish needs evidence does not hand out silent permission to skip the checks.
    if any(a in ("--no-verify", "-n") for a in arguments):
        return False
    return _inside(root, here) and _paths_are_ours(root, here, ["git", *arguments[1:]])


def _walk(here: Path, root: Path, argv: list[str], clone: Path | None = None) -> Path | None:
    """The directory the next segment runs in, or None when it leaves this clone.

    The CLONE and not this session's own tree. Walking into the main checkout changes
    nothing by itself, and refusing to vouch for it left a session that had stepped out
    unable to be told, without a prompt, how to step back. Reads and writes are still
    judged against the session's OWN root, so a `cd` elsewhere buys nothing beyond the
    move itself.
    """
    if _program(argv) != "cd":
        return here
    targets = _arguments(argv)
    if not targets:
        return None
    moved = _resolve(here, targets[0])
    if moved is None:
        return None
    return moved if _inside(root, moved) or (clone and _inside(clone, moved)) else None


def for_bash(ctx: GitContext, line: str, test_command: list[str], cwd: Path | None = None) -> str:
    """Why this shell line needs no prompt, or "" to leave the decision where it was."""
    parsed = shellcmd.segments(line)
    if not parsed:
        return ""
    root = ctx.worktree_root.resolve()
    here = (cwd or ctx.worktree_root).resolve()
    if not _inside(root, here):
        return ""
    # The main checkout of this clone, reached without asking git: worktrees live under it.
    try:
        clone = ctx.common_dir.parent.resolve()
    except OSError:
        clone = None

    reasons: list[str] = []
    for argv in parsed:
        here, reason = _judge(root, here, clone, argv, test_command)
        if here is None:
            return ""
        if reason and reason not in reasons:
            reasons.append(reason)
    # A line that is ONLY navigation vouched for nothing, because navigation contributes no
    # reason — so `cd <my own worktree>`, `pwd` and `true` all went to the classifier, and
    # the founder was asked to authorise a session walking back into the tree this gate had
    # ordered it into (#123).
    return "\n".join(reasons) if reasons else MOVE


def _judge(root: Path, here: Path, clone: Path | None, argv: list[str],
           test_command: list[str]) -> tuple:
    """One segment: where the next one runs, and why this one needs no permission.

    `(None, "")` ends the vouch for the whole line — one unqualified segment takes the
    line with it, because `allow_tool` approves the line and there is no half of it to
    approve.
    """
    if not _accountable(argv):
        return None, ""
    if _program(argv) in _NAVIGATION:
        return _walk(here, root, argv, clone), ""
    reason = _classify(root, here, argv, test_command)
    return (here, reason) if reason else (None, "")


def _classify(root: Path, here: Path, argv: list[str], test_command: list[str]) -> str:
    if _own_command(argv):
        return OWN
    if _orders_a_worktree(argv):
        return WORKTREE
    if _reads(root, here, argv):
        return READ
    if _checks(root, here, argv, test_command):
        return SUITE
    if _commits_here(root, here, argv):
        return WRITE
    return ""


def for_write(ctx: GitContext, session_id: str, paths: list[Path]) -> str:
    """Why writing these paths needs no prompt, or "" to leave the decision where it was."""
    from . import gitpolicy

    if not paths:
        return ""
    for path in paths:
        if _SECRETISH.search(path.as_posix()):
            return ""
        if (not gitpolicy.owned_by_session(ctx, path)
                and gitpolicy.provisioned_tree_of(ctx, session_id, path) is None):
            return ""
    return WRITE


OWN = (
    "claude-bestpractice: this is one of this plugin's own commands, run from the copy that "
    "is installed here. Every refusal it prints names one of these as the way out, so "
    "asking the founder to authorise it is the gate arguing with its own instructions."
)

# This plugin's own `bin/`, resolved from where this file actually is rather than from a
# name — a `claude-bp` on PATH belonging to some other install is not this one.
_OWN_BIN = Path(__file__).resolve().parents[2] / "bin"

# `adopt` moves ANOTHER tool's hook entries out of the founder's settings. Everything else
# here writes only this plugin's own state, or — in `policy`'s case — facts it re-derives
# from the repository. Rewriting somebody else's configuration is not in that family and
# is left to the permission layer on purpose.
_NOT_OURS_TO_VOUCH = {"adopt"}


def own_command(line: str) -> bool:
    """Is this shell line entirely this plugin's own CLI?

    Public because the tool-call ceiling needs it: a ceiling that also refuses the command
    that raises it is not a ceiling, it is the end of the session.
    """
    parsed = shellcmd.segments(line)
    return bool(parsed) and all(
        _program(argv) in _NAVIGATION or _own_command(argv) for argv in parsed
    )


def _own_command(argv: list[str]) -> bool:
    """Is this the plugin's own CLI, from this install?

    The gate's refusals name these commands: `claude-bp-plan add`, `claude-bp-plan claim`,
    `claude-bp-ci status`, `claude-bp set`. A refusal that names a command the founder is then
    asked to authorise is the interruption `allow_tool` exists to remove — and for
    `claude-bp policy --apply` it was worse than an interruption: the classifier refused
    the command whose whole purpose is that the agent, not the founder, maintains the file
    the classifier reads (#116).
    """
    if not _from_our_install(argv):
        return False
    return not any(arg in _NOT_OURS_TO_VOUCH for arg in _arguments(argv))


def _from_our_install(argv: list[str]) -> bool:
    """Does `argv[0]` resolve to a binary in THIS install's `bin/`?

    `argv[0]` and not `_program`, which is the basename: a bare `claude-bp` has to be
    resolved through PATH, and a `claude-bp` on PATH belonging to some other install must
    not answer for this one.
    """
    import shutil

    token = argv[0] if argv else ""
    if not token:
        return False
    found = token if ("/" in token or "\\" in token) else (shutil.which(token) or "")
    if not found:
        return False
    try:
        resolved = Path(found).resolve()
    except OSError:
        return False
    return resolved.parent == _OWN_BIN and resolved.name.startswith("claude-bp")


# `discard_changes` is absent by design: it is the one exit that destroys work, and no
# convention this plugin publishes asks for it.
_EXIT_ACTIONS = {"keep", "remove"}


def for_tool(ctx: GitContext, tool_name: str, tool_input: dict) -> str:
    """Why this structured call needs no prompt, or "" to leave the decision where it was.

    `EnterWorktree` was vouched for and `ExitWorktree` was not, so the founder authorised
    the last step of a workflow whose first step the plugin had approved on their behalf
    seconds earlier — and `git worktree remove` through Bash, the identical action in the
    other spelling, was already silent. Reported as #110.
    """
    if tool_name != "ExitWorktree":
        return ""
    if tool_input.get("discard_changes"):
        return ""
    action = str(tool_input.get("action") or "keep").lower()
    if action not in _EXIT_ACTIONS:
        return ""
    if action == "remove":
        from . import delivery

        # The same condition `git worktree remove` enforces by refusing, asked before the
        # call instead of after it: a tree holding uncommitted work is not one the founder
        # agreed to lose by installing this plugin.
        try:
            if delivery.dirty(ctx):
                return ""
        except OSError:
            return ""
    return EXIT


def surface(ctx: GitContext, test_command: list[str]) -> list[str]:
    """What this plugin vouches for here, so the founder can read it rather than find it.

    A rule that is applied but never published is one the founder has to reverse-engineer
    from behaviour, which is how they ended up writing it out by hand in the first place.
    """
    detected = " ".join(test_command) if test_command else "none detected"
    return [
        f"reads inside {ctx.worktree_root.name}/ that write nothing (git log/diff/status, cat, grep)",
        f"this project's checks in any spelling (detected: {detected})",
        "git worktree add/remove/list, entering and leaving one, and writes and commits "
        "in this session's own tree",
        "opening a pull request, and merging one this gate has just found no blockers for",
        "this plugin's own commands, which are what its refusals tell you to run",
        "moving around inside this repository: cd, pwd, and doing nothing at all",
        "each segment of a compound command judged alone; one unvouched segment ends it",
        "not: the network, production, git push, credentials, anything outside this tree",
    ]
