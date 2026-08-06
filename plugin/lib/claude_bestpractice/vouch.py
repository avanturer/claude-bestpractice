"""Calls this plugin ORDERED, and therefore has no business making the founder authorise.

`allow_tool` ends the permission pipeline, so nothing here may run before the gates: every
function in this module is consulted at the very end of `pre-tool`, after every refusal has
already had its say. A vouch is the last word on a call the plugin was going to allow
anyway — never a way past one of its own rules.

Measured on a real machine over three days: 35 classifier blocks across six sessions, of
which three were production actions worth asking about. Six were the project's own checks,
demanded by this plugin's own evidence gate; the rest was ordinary work inside a worktree
this plugin had handed the session. A gate that orders a move and then lets the permission
layer interrogate it is arguing with itself, and the founder pays for the argument (#99).

Two boundaries hold this narrow. Production stays out — ssh to a host, a store submission,
a force push are nobody's orders but the founder's, and none of them can match a rule here.
And an unparseable line vouches for NOTHING: `shellcmd.acting` returns empty there, which
this reads as "say nothing and let the permission layer decide", the behaviour that
predates this file. A line crafted to break the tokeniser must not become a line that walks
past the prompt.
"""

from __future__ import annotations

from pathlib import Path

from . import shellcmd
from .gitctx import GitContext

# Moving around is not doing anything, and the founder's real commands are compound:
# `cd <tree> && git worktree add …` is the shape the refusal text itself suggests. Without
# these the vouch would only ever fire for a line nobody types.
_NAVIGATION = {"cd", "pwd", "pushd", "popd", "true", ":"}

# `add` and `remove` are the move the worktree refusal orders and then names; `list` is how
# an agent finds out where it already stands. Nothing else — `git worktree` is not a family
# of commands to be waved through, it is these three.
_WORKTREE_VERBS = {"add", "remove", "list"}

WORKTREE = (
    "claude-bestpractice: this gate orders a worktree and names `git worktree` for making "
    "one, so the CLI spelling of the move needs no further permission — `EnterWorktree` "
    "already has none."
)
SUITE = (
    "claude-bestpractice: the evidence gate refuses a finish without a run of this exact "
    "command, which the plugin detected itself. Demanding a suite and then interrogating "
    "the suite is the plugin arguing with itself."
)
WRITE = (
    "claude-bestpractice: this worktree was created by this plugin for this session, "
    "seconds after a gate refused a write for not being in one. Writing here is the move "
    "that was ordered."
)


def _git_arguments(argv: list[str]) -> list[str]:
    """`git -C tree worktree add …` → `["worktree", "add", …]`. Empty when it is not git.

    The global options are stepped over rather than matched on, because a subcommand read
    positionally is a subcommand `-C` can move: `git -C x worktree add` and `git worktree
    add` are one command, and a rule that only knows the second reads the first as a
    program called `-C` — which fails towards asking, but asks about the exact line this
    exists to stop asking about.
    """
    if not argv or argv[0].rsplit("/", 1)[-1] != "git":
        return []
    rest = argv[1:]
    while rest and rest[0].startswith("-"):
        carries_a_value = rest[0] in ("-C", "--git-dir", "--work-tree", "--namespace")
        rest = rest[2:] if carries_a_value and len(rest) > 1 else rest[1:]
    return rest


def _orders_a_worktree(argv: list[str]) -> bool:
    arguments = _git_arguments(argv)
    return len(arguments) >= 2 and arguments[0] == "worktree" and arguments[1] in _WORKTREE_VERBS


def for_bash(line: str, test_command: list[str]) -> str:
    """Why this shell line needs no prompt, or "" to leave the decision where it was.

    Every acting command has to be one this plugin ordered. One it cannot name — a deploy,
    an ssh, an `rm` appended after the vouched part — and the whole line falls through,
    because `allow_tool` approves the LINE and there is no half of it to approve.
    """
    acting = shellcmd.acting(line)
    if not acting:
        return ""
    reasons: list[str] = []
    for argv in acting:
        if argv[0].rsplit("/", 1)[-1] in _NAVIGATION:
            continue
        if _orders_a_worktree(argv):
            reason = WORKTREE
        elif test_command and argv == list(test_command):
            # Exactly as detected, unmodified. `pytest -q tests/one.py` is a defensible
            # thing to run and not a thing this plugin asked for, and the gap between
            # those two is where a vouch stops being a vouch.
            reason = SUITE
        else:
            return ""
        if reason not in reasons:
            reasons.append(reason)
    return "\n".join(reasons)


def for_write(ctx: GitContext, session_id: str, paths: list[Path]) -> str:
    """Why writing these paths needs no prompt, or "" to leave the decision where it was."""
    from . import gitpolicy

    if not paths:
        return ""
    for path in paths:
        if gitpolicy.provisioned_tree_of(ctx, session_id, path) is None:
            return ""
    return WRITE
