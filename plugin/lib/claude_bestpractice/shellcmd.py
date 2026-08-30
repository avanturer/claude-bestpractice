"""What a shell line RUNS, rather than what it contains.

Gates that matched a regex against the whole command line were deciding on text. `echo`
of the merge invocation was refused as a merge; `grep` for it in documentation was refused
as a merge; a script whose JSON payload happened to contain it was refused, which is how
the tool for investigating the gate became blocked by the gate (#76).

Reading is not doing, and quoting is not doing. This splits the line into the commands it
would actually execute and hands back their argv, so a gate can ask "is this program, with
this subcommand" instead of "does this string occur anywhere".

Fails towards the old behaviour on purpose: an unparseable line returns nothing, and every
caller is written to fall back to the substring match there. A line crafted to break the
tokeniser must not become a line that walks past the gate.
"""

from __future__ import annotations

import re
import shlex

# The operators bash uses to end one command and start another. `shlex` with
# `punctuation_chars` emits these as their own tokens, and — the part that matters —
# leaves them inside a token when they are quoted, so `echo 'a && b'` stays one command.
_SEPARATORS = {"&&", "||", ";", "|", "|&", "&", "\n", ";;", ";&", ";;&",
              "(", ")", "<(", ">(", "`"}

# The subset that bash requires a command on BOTH sides of. A line ending on one of these —
# or starting on one — is a SYNTAX ERROR, not a shorter line, and the difference is the
# whole point: dropping the dangling operator made `make test &&` parse identically to
# `make test`, so `vouch` approved it and `allow_tool` ended the permission pipeline before
# Claude Code 2.1.246's own rule ("always require approval for malformed commands with a
# dangling && or ||") could be applied. A parse this module and the shell disagree about is
# precisely what must never become a vouch.
#
# `;`, `&` and a newline are deliberately NOT here: `ruff check src/ ;` and `sleep 1 &` are
# valid shell, and refusing them would cost real work to fix nothing.
_NEEDS_A_FOLLOWER = {"&&", "||", "|", "|&"}

# Shell this tokeniser does not model, and the reason it declines the whole line rather
# than reading around it. Two families, found the same way and wrong the same way — a
# confident argv over a line that runs something else entirely.
#
# CASE TERMINATORS. `shlex` returns each as one ordinary token:
#
#   `a ;; b`                      bash rejects the line; this read `a` with two arguments
#   `case x in a) echo 1;; esac`  bash accepts it; this read ONE command named `case`
#
# SUBSTITUTION AND SUBSHELLS, which is the one that mattered. Measured against the armed
# merge gate: `gh pr merge 1 --squash` was denied and `FOO=$(gh pr merge 1) echo hi` was
# ALLOWED, with bash shown to execute the substitution. The program position parsed as
# `(`, so `runs()` never matched `gh` — and `pullrequest._gh_subcommand` never reached the
# regex fallback that WOULD have caught it. A session could merge without the founder's
# `+merge`, which is the whole of decision 0006 walked past.
#
# QUOTING IS THE DISCRIMINATOR, and it survives tokenisation: `echo '$(gh pr merge 1)'`
# keeps the substitution inside ONE token and is still read as text. That is why this is
# asked of the token stream and never of the raw line — matching text is #76, the defect
# this module was written to end. The backtick earns its place in `punctuation_chars` for
# the same reason: unquoted it becomes its own token, quoted it stays inside one.
#
# The cost, stated: `find . \( -name '*.py' \) -print` is declined too, because an escaped
# paren and a real one are the same token here. A declined line is not vouched for and its
# gates fall back to the substring match — the direction that refuses rather than allows,
# and the one this module already documents as its failure mode.
_UNMODELLED = {";;", ";&", ";;&", "(", ")", "<(", ">(", "`"}

# Programs that run their argument as the real command, so the thing being gated is one
# position further along. The same list Claude Code itself strips, plus `env`, which takes
# assignments before the program name.
_WRAPPERS = {"timeout", "time", "nice", "nohup", "stdbuf", "command", "builtin", "noglob", "env"}

# `command -v gh` asks where gh is; it does not run it.
_LOOKUP_FLAGS = {"-v", "-V", "-p"}


def commands(line: str) -> list[list[str]]:
    """argv of every command in `line`, wrappers and env assignments stripped.

    Empty when the line cannot be tokenised, which the callers treat as "ask the old way".
    """
    return [stripped for stripped in (_unwrap(argv) for argv in segments(line)) if stripped]


def segments(line: str) -> list[list[str]]:
    """Every command in `line` exactly as written — nothing stripped, nothing unwrapped.

    `commands` drops `FOO=bar` and `timeout 30` to find the program a gate should judge.
    That is the right direction for a REFUSAL and the wrong one for a vouch: stripping
    `GIT_PAGER='sh -c …'` off `git log` yields a read to approve and throws away the half
    that runs. A caller that vouches has to see the line whole and decline what it cannot
    account for, so it gets this instead.
    """
    # `not line` and not `not line.strip()`: whitespace-only tokenises to nothing and
    # falls out empty anyway, and this still answers for a None a caller should not pass.
    if not line:
        return []
    try:
        # The backtick is added to the default `();<>|&` so an unquoted one is its own token.
        lexer = shlex.shlex(line, posix=True, punctuation_chars="();<>|&`")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    out: list[list[str]] = []
    current: list[str] = []
    # Whether the separator just seen still owes us a command. Tracked rather than checked
    # at the end, so `a && && b` and `&& a` are caught for the same reason `a &&` is.
    owed = False
    for token in tokens:
        if token in _SEPARATORS:
            if token in _UNMODELLED or (token in _NEEDS_A_FOLLOWER and not current):
                return []
            out.append(current)
            current = []
            owed = token in _NEEDS_A_FOLLOWER
            continue
        current.append(token)
        owed = False
    if owed:
        return []
    out.append(current)
    # Emptied in one place rather than guarded in two: `a ;; b` and a trailing `;` both
    # leave a gap, and neither is a command to hand a gate.
    return [argv for argv in out if argv]


def _unwrap(argv: list[str]) -> list[str]:
    """Drop leading `FOO=bar` assignments and wrapper programs.

    Bounded rather than recursive: a line wrapping a wrapper eight deep is not a shape any
    real command takes, and an unbounded walk over attacker-supplied argv is a loop.
    """
    for _ in range(8):
        if not argv:
            return []
        # `command -v gh` LOOKS UP gh rather than running it, so the strip must not turn a
        # query into the thing being queried. Named exactly, because the general "a wrapper
        # followed by an option is not a wrapper" reading also swallowed `nice -n 5 gh …`.
        if argv[0] == "command" and len(argv) > 1 and argv[1] in _LOOKUP_FLAGS:
            return argv
        if _is_assignment(argv[0]):
            argv = argv[1:]
        elif argv[0] in _WRAPPERS and len(argv) > 1:
            argv = _past_wrapper_arguments(argv[1:])
        else:
            return argv
    return argv


def _is_assignment(token: str) -> bool:
    """`FOO=bar` before the program name, which the shell consumes as environment."""
    name, sep, _ = token.partition("=")
    return bool(sep) and bool(name) and "/" not in name


# `timeout 30`, `nice -n 5`, `stdbuf -o0` — a wrapper carrying its own argument. Dropping
# only the wrapper's name left `30` standing where the program should be, and
# `timeout 30 gh pr merge` read as a command named 30. A missed detection is the dangerous
# direction for every caller here, so these are consumed generously.
_WRAPPER_ARGUMENT = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")


def _past_wrapper_arguments(argv: list[str]) -> list[str]:
    while argv and (argv[0].startswith("-") or _WRAPPER_ARGUMENT.match(argv[0])):
        argv = argv[1:]
    return argv


def runs(line: str, program: str, *subcommand: str) -> list[list[str]]:
    """Every command in `line` that runs `program` with `subcommand` as its first words.

    The program is compared on its basename, so `/usr/bin/gh` and `gh` are the same call.
    """
    found: list[list[str]] = []
    for argv in commands(line):
        name = argv[0].rsplit("/", 1)[-1]
        if name != program:
            continue
        if list(argv[1:1 + len(subcommand)]) != list(subcommand):
            continue
        found.append(argv)
    return found


# Programs whose arguments are data by construction. `echo` writes nothing, and the rest
# read. A gate that matches flags anywhere in a line refused `echo "deploy --prod"` as a
# production deploy — the same defect as #76, one gate over, and unreported because nobody
# had tried to write about a deploy yet.
READERS = {
    "echo", "printf", "cat", "grep", "rg", "egrep", "fgrep", "less", "more",
    "head", "tail", "wc", "sed", "awk", "cut", "sort", "uniq", "diff", "true",
}


def acting(line: str) -> list[list[str]]:
    """The commands in `line` that could do something, readers dropped.

    Empty for an unparseable line, which every caller treats as "decide the old way" —
    failing towards the previous behaviour rather than towards permission.
    """
    return [argv for argv in commands(line) if argv[0].rsplit("/", 1)[-1] not in READERS]
