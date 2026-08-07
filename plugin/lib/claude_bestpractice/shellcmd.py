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
_SEPARATORS = {"&&", "||", ";", "|", "|&", "&", "\n"}

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
    if not line or not line.strip():
        return []
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    out: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if current:
                out.append(current)
            current = []
            continue
        current.append(token)
    if current:
        out.append(current)
    return out


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
