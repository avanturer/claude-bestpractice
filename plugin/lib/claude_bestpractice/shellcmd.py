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

import functools
import re
import shlex
from typing import Iterator

# The operators bash uses to end one command and start another. `shlex` with
# `punctuation_chars` emits these as their own tokens, and — the part that matters —
# leaves them inside a token when they are quoted, so `echo 'a && b'` stays one command.
# A newline is the other one, and `shlex` reads it as a space: `_one_line` turns each that
# ends a command into the `;` it is before this ever sees a token.
_SEPARATORS = {"&&", "||", ";", "|", "|&", "&", ";;", ";&", ";;&",
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
# the same reason: unquoted it becomes its own token, quoted it stays inside one. A word
# that is only a quoted or escaped operator, `';'` or `\(`, is a `_Quoted` word, never one.
_UNMODELLED = {";;", ";&", ";;&", "(", ")", "<(", ">(", "`"}

# The operators that hand a pipeline's status to the command after them.
_PIPES = ("|", "|&")

# What `shlex` reads the same as something else, told apart while the line is still text. A
# word with a quoted or escaped part is a word, whatever its characters: `'>'` is an argument
# and `';'` ends nothing. A number written against `<` or `>` with nothing between is that
# redirection's descriptor, which no program receives: bash passes the `5` on in `gh pr merge
# 5 >out` and not in `gh pr merge 5>out`, measured on bash 5.2, and `shlex` read both as `5`,
# `>`, `out`. Each is marked by a character `shlex` keeps inside the word, and the word comes
# back as the str it was with its kind on it, so a reader comparing text reads what it did.
_QUOTED_MARK = "﷐"
_DESCRIPTOR_MARK = "﷑"

# A descriptor as bash takes one: digits, or the `{name}` it assigns one to, as a whole word.
# A quoted or expanded part makes it an argument: `'5'>out` and `5$n>out` pass it on.
_DESCRIPTOR = re.compile(r"(?<![^\s;&|()<>])(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})")

# A redirection's operator, as `shlex` cuts one out of an unquoted run of punctuation.
_REDIRECTION = re.compile(r"[<>&|]*[<>][<>&|]*")


class _Quoted(str):
    """A word with a quoted or escaped part, which is never an operator."""


class _Descriptor(str):
    """The number or `{name}` a redirection is written against, which no program receives."""

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
    # Copies, so a caller that edits what it was handed cannot change what the next one reads.
    return [list(argv) for argv, _ in _linked(line)]


# Read ONCE per hook call. Every gate asks its own question of the same line — the pull
# request gate twice, the staging rule, the history rule, the commit rule, the push gate's
# verbs, the ceiling and the vouch — and each used to tokenise it afresh: eleven readings of
# one `git commit`, which is what made a long command cost seconds.
@functools.lru_cache(maxsize=8)
def _linked(line: str) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Every command in `line` as written, with the operator that ends it, "" for none.

    A gap the separators leave, as in `a ;; b` or after a trailing `;`, is no command to hand
    a gate, and is dropped.
    """
    try:
        tokens = _tokens(line)
    except ValueError:
        return ()

    out: list[tuple[tuple[str, ...], str]] = []
    current: list[str] = []
    # Whether the separator just seen still owes us a command. Tracked rather than checked
    # at the end, so `a && && b` and `&& a` are caught for the same reason `a &&` is.
    owed = False
    for token in tokens:
        if not _separates(token):
            current.append(token)
            owed = False
            continue
        if token in _UNMODELLED or (token in _NEEDS_A_FOLLOWER and not current):
            return ()
        if current:
            out.append((tuple(current), token))
        current = []
        owed = token in _NEEDS_A_FOLLOWER
    if owed:
        return ()
    return (*out, (tuple(current), "")) if current else tuple(out)


def _separates(token: str) -> bool:
    """Does this token end a command? A quoted `;` or `|` is an argument like any other."""
    return token in _SEPARATORS and not isinstance(token, _Quoted)


def _tokens(line: str) -> list[str]:
    text = _marked(_one_line(_without_substituted_documents(line)))
    try:
        words = _lexed(_Lexer, text)
    except (TypeError, AttributeError):
        # A Python whose `shlex` builds its token in a way `_Token` does not answer for: the
        # stock lexer reads the line exactly the same, only in quadratic time.
        words = _lexed(shlex.shlex, text)
    return [_typed(word) for word in words]


def _marked(text: str) -> str:
    """`text` with each quoted part and each redirection's descriptor marked for `_typed`.

    A line already carrying either mark cannot be read this way, and is not read at all.
    """
    if _QUOTED_MARK in text or _DESCRIPTOR_MARK in text:
        raise ValueError("the line carries a character its words are marked with")
    out: list[str] = []
    at = 0
    for piece in _PIECE.finditer(text):
        if piece.lastgroup == "quoted":
            out.append(text[at:piece.start() + 1] + _QUOTED_MARK)
            at = piece.start() + 1
        elif piece.lastgroup == "plain":
            for found in _DESCRIPTOR.finditer(text, piece.start(), piece.end()):
                if text[found.end():found.end() + 1] in ("<", ">"):
                    out.append(text[at:found.end()] + _DESCRIPTOR_MARK)
                    at = found.end()
    out.append(text[at:])
    return "".join(out)


def _typed(word: str) -> str:
    """A word as `shlex` read it, with its mark taken off and its kind put on."""
    if _DESCRIPTOR_MARK in word:
        return _Descriptor(word.replace(_DESCRIPTOR_MARK, ""))
    if _QUOTED_MARK in word:
        return _Quoted(word.replace(_QUOTED_MARK, ""))
    return word


def _lexed(lexer_class: type, line: str) -> list[str]:
    # The backtick is added to the default `();<>|&` so an unquoted one is its own token.
    lexer = lexer_class(line, posix=True, punctuation_chars="();<>|&`")
    lexer.whitespace_split = True
    # Comments are `_one_line`'s. `shlex` starts one at a `#` in the MIDDLE of a word, where
    # bash does not, and reads to the end of the line: `echo a#b && rm -rf src` came back
    # as `echo a` alone, a read the vouch approved with the deletion riding along.
    lexer.commenters = ""
    return list(lexer)


# A line cut into the pieces that decide where a command ends: a quoted span, where a
# newline is data; a backslash escape, where a newline joins two lines into one; a heredoc
# operator, after which the next lines are a document and not commands; a comment, which
# starts only where a word could; a newline; and everything else.
_PIECE = re.compile(
    r"""(?P<quoted>'[^']*'?|"(?:\\.|[^"\\])*"?)"""
    r"""|(?P<escape>\\.?)"""
    r"""|(?P<heredoc><<-?[ \t]*(?:'[^'\n]*'|"[^"\n]*"|\\?[^\s;&|<>()'"]+))"""
    r"""|(?P<comment>(?<![^\s;&|()<>])#[^\n]*)"""
    r"""|(?P<newline>\n)"""
    r"""|(?P<plain><<<|[^'"\\<#\n]+|[<#])""",
    re.S,
)

# A newline after one of these continues the command instead of ending it.
_CONTINUED = ("&&", "||", "|", "|&")

# A document read inside `$(…)`: `--body "$(cat <<'EOF' … EOF)"`, the shape nearly every pull
# request and issue body is written in. The quoted word around it ended at the first `"` in
# the document, so a body that quoted anything declined the whole line and every reader of it
# fell back to guessing: an issue quoting `gh pr merge 794` was refused as that merge, and
# `cd <tree> && gh pr create` was filed on the branch the session stood on (#248). Only the
# document `cat` prints and nothing else touches: its delimiter is quoted, so bash expands
# nothing in it, and it is text the command reads, set aside here as `_one_line` sets aside
# every document it can see. Fed to `sh`, or piped on to one, it is a script that runs, and
# it stays. It ends where bash ends it: on a line of the delimiter alone, after the tabs `<<-`
# strips.
_SUBSTITUTED_DOCUMENT = re.compile(
    r"(?P<opened>\$\([ \t]*cat[ \t]+<<(?P<dash>-)?[ \t]*(?P<quote>['\"])(?P<tag>\w+)(?P=quote)"
    r"[ \t]*\n)(?P<document>.*?)(?P<closed>^(?(dash)\t*)(?P=tag)$)",
    re.S | re.M,
)


def _without_substituted_documents(line: str) -> str:
    """`line` with the document of each quoted heredoc read inside `$(…)` taken out."""
    if "<<" not in line:
        return line
    return _SUBSTITUTED_DOCUMENT.sub(_set_aside, line)


def _set_aside(found: re.Match) -> str:
    """The heredoc without its document, or as it was when a shell may end it sooner.

    Bash 5.2 ends one at `EOF)`, which is not a line of the delimiter alone, so the match runs
    on to the next `EOF` and would hide whatever the line runs in between. Left whole, the
    line is read the old way, which errs towards seeing a command rather than missing one.
    """
    if re.search(rf"^[ \t]*{found['tag']}\b", found["document"], re.M):
        return found[0]
    return found["opened"] + found["closed"]


def _one_line(line: str) -> str:
    """`line` with each newline that ends a command spelled as the `;` it is, and each
    heredoc's document taken out.

    `shlex` reads a newline as a space, so every command on a later line became arguments
    of the first: `git log --oneline` + newline + `curl -X POST https://… -d @.env` was
    vouched for as a read of the log, `git status` + newline + `rm -rf src` as a status,
    and `git add -A` on its own line was never seen by the rule about staging everything.
    A heredoc's lines stay out of it the other way: they are what a command reads, and a
    script being written that contains `git add -A` is not the session staging anything.
    """
    return _read(line)[0]


def _read(line: str) -> tuple[str, list[tuple[str, str]]]:
    """`line` as `_one_line` gives it, and each document it took out, after the operator that
    opened it: `[("<<'EOF'", "gh pr merge 5\\n")]`."""
    out: list[str] = []
    opened: list[str] = []
    documents: list[tuple[str, str]] = []
    last = ""
    at = 0
    while at < len(line):
        piece = _PIECE.match(line, at)
        at = piece.end()
        text = _spoken(piece)
        if piece.lastgroup == "newline":
            text = " " if last.endswith(_CONTINUED) else " ; "
            at, read = _past_documents(line, at, opened)
            documents.extend(zip(opened, read))
            opened = []
        elif piece.lastgroup == "heredoc":
            opened.append(text)
        out.append(text)
        last = text.rstrip() or last
    return "".join(out), documents


def _spoken(piece: re.Match) -> str:
    """One piece as `shlex` should read it: a comment and a `\\`-newline gone, and an escaped
    character quoted instead — so a `|` left at the end of the text is always the operator,
    never the `\\|` that is a character of a word."""
    text = piece.group()
    if piece.lastgroup == "comment" or text == "\\\n":
        return ""
    if piece.lastgroup == "escape" and len(text) == 2:
        return "\"'\"" if text[1] == "'" else f"'{text[1]}'"
    return text


def _delimiter(heredoc: str) -> str:
    """`<<-'EOF'` → `EOF`, the line that ends the document."""
    return heredoc.lstrip("<").lstrip("-").strip().strip("'\"").lstrip("\\")


def _past_documents(line: str, at: int, opened: list[str]) -> tuple[int, list[str]]:
    """Where the commands resume after the documents the heredocs `opened` start at `at`, and
    the text of each."""
    read: list[str] = []
    for heredoc in opened:
        end = re.compile(rf"^[ \t]*{re.escape(_delimiter(heredoc))}[ \t]*$", re.M).search(line, at)
        read.append(line[at:end.start()] if end else line[at:])
        at = end.end() + 1 if end else len(line)
    return at, read


class _Token:
    """The word `shlex` is building, grown in place rather than copied per character.

    `shlex.read_token` extends `self.token` one character at a time with `+=`, and a str
    held on an attribute is copied whole every time — so one long quoted argument costs
    time in the square of its length. Reading a 256k-character commit message took 1.3 s
    where a line of short words that long took 0.1 s, on a hook that runs before every tool
    call, and a heredoc holding one stray apostrophe is exactly such an argument.
    `read_token` (the same in every Python this plugin supports) only ever assigns the
    token, extends it, tests it for truth and compares it with `''`, and those four are what
    this answers; anything else raises, and `_tokens` reads the line with the stock lexer.
    """

    __slots__ = ("_parts",)

    def __init__(self, text: str = "") -> None:
        self._parts = [text] if text else []

    def __iadd__(self, text: str) -> _Token:
        if text:
            self._parts.append(text)
        return self

    def __bool__(self) -> bool:
        return bool(self._parts)

    def __eq__(self, other: object) -> bool:
        return str(self) == other

    def __str__(self) -> str:
        return "".join(self._parts)


class _Lexer(shlex.shlex):
    """`shlex.shlex` building each word in a `_Token`, and handing back a plain str."""

    @property
    def token(self) -> _Token:
        return self._token

    @token.setter
    def token(self, value: str | _Token) -> None:
        self._token = value if isinstance(value, _Token) else _Token(value)

    def read_token(self) -> str | None:
        word = super().read_token()
        return None if word is None else str(word)


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


# Programs that run a command line handed to them as ONE string, which is read again here.
_SCRIPT_RUNNERS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish", "eval", "su", "ssh",
                   "watch", "script"}


def without_redirections(words: list[str]) -> list[str]:
    """`words` as the program receives them: each redirection gone, with its target and the
    descriptor written against it. `gh pr merge 5 >out` passes `5` on, `gh pr merge 5>out` and
    `gh pr merge 2>&1` pass nothing, and `-b '> said'` is a flag and its value."""
    kept: list[str] = []
    target = False
    for word in words:
        if target or isinstance(word, _Descriptor):
            target = False
        elif not isinstance(word, _Quoted) and _REDIRECTION.fullmatch(word):
            target = True
        else:
            kept.append(word)
    return kept


def masked_by(line: str, argv: list[str]) -> str | None:
    """The operator that lets `line` exit 0 when its command `argv` failed, "" when none does.
    None when `argv` is not a command of `line`.

    A pipe after it hands the line the next command's status; `;`, `||` or `&` after it, or a
    line sent to the background, goes on whatever it did; and `||` before its pipeline skips
    it when what came first succeeded. `&&` after it carries its failure to the end, past
    every pipeline the failure skips, and `;` before it or after the last command joins
    nothing it depends on.
    """
    links = _linked(line)
    at = next((at for at, (words, _) in enumerate(links) if _unwrap(list(words)) == argv), None)
    if at is None:
        return None
    joints = [joint for _, joint in links]
    if joints[at] in _PIPES:
        return joints[at]
    for joint in joints[at:-1]:
        if joint not in ("&&", *_PIPES):
            return joint
    if joints[-1] == "&":
        return "&"
    before = next((joint for joint in reversed(joints[:at]) if joint not in _PIPES), "")
    return before if before == "||" else ""


def hidden(line: str, program: str, *subcommand: str) -> bool:
    """Does `line` run `program` with `subcommand` from somewhere `runs` does not report?

    `runs` reads the first word of each command, so `gh pr merge` in a loop, a condition or a
    group, behind `!`, handed to `xargs`, `sudo` or `exec`, in a string `sh -c`, `eval` or
    `ssh` runs, in a document a shell reads as its script, or in a substitution bash expands,
    `"$(…)"` included, was no merge at all to the gate that judges merges, and `for n in 1 2;
    do gh pr merge $n; done` went through on nobody's word. Here a word that is not the program
    in front of it is no cover, a string a shell runs is read as a line, and a reader's
    arguments are data, as everywhere else.
    """
    wanted = [program, *subcommand]
    for argv in commands(line):
        names = [word.rsplit("/", 1)[-1] for word in argv]
        if names[0] in READERS:
            continue
        if any(names[at:at + len(wanted)] == wanted for at in range(1, len(names))):
            return True
        if names[0] in _SCRIPT_RUNNERS and any(_runs_text(word, wanted) for word in argv[1:]):
            return True
    return _scripted(line, wanted) or _substituted(line, wanted)


def _runs_text(text: str, wanted: list[str]) -> bool:
    """Does `text`, run as a shell line, run `wanted`? Matched as words where it cannot be read."""
    if not commands(text):
        return _words(wanted).search(text) is not None
    return bool(runs(text, *wanted)) or hidden(text, *wanted)


def _scripted(line: str, wanted: list[str]) -> bool:
    """Does a document or a here-string `line` hands a shell as its script run `wanted`?

    `bash <<'EOF'`, `cat <<'EOF' | sh`, `ssh host <<'EOF'` and `bash -c "$(cat <<'EOF' …)"`: the
    document is the script, and every reading here sets documents aside as text a command
    reads. Read as a line wherever a script runner reads it, or reads what it was written for.
    """
    links = _linked(line)
    documents = iter(_read(_without_substituted_documents(line))[1])
    for at, (words, _) in enumerate(links):
        written = _written_for(words, documents, line)
        if written and _a_shell_reads(links, at) and \
                any(_runs_text(text, wanted) for text in written):
            return True
    return False


def _written_for(words: tuple[str, ...], documents: Iterator[tuple[str, str]],
                 line: str) -> list[str]:
    """The text `line` writes out for a command to read: the document of each heredoc it opens,
    taken from `documents` in order, each here-string's word, and the documents `cat` prints
    inside its words."""
    written: list[str] = []
    for word, following in zip(words, [*words[1:], ""]):
        if isinstance(word, _Quoted):
            if "<<" in word:
                written.extend(found["document"] for found in _SUBSTITUTED_DOCUMENT.finditer(line))
        elif word == "<<":
            written.append(next(documents, ("", ""))[1])
        elif word == "<<<":
            written.append(following)
    return written


def _a_shell_reads(links: tuple[tuple[tuple[str, ...], str], ...], at: int) -> bool:
    """Is command `at` a script runner, or does its output reach one through pipes?"""
    for words, joint in links[at:]:
        names = [word.rsplit("/", 1)[-1] for word in _unwrap(list(words))]
        if names and names[0] not in READERS and any(name in _SCRIPT_RUNNERS for name in names):
            return True
        if joint not in _PIPES:
            return False
    return False


def _substituted(line: str, wanted: list[str]) -> bool:
    """Does a command substitution bash expands in `line` run `wanted`?

    Bash expands one unquoted, inside double quotes, and in the document of a heredoc whose
    delimiter is not quoted. Single quotes keep it text, and so does a backslash before the
    `$` or the backtick: `echo '$(gh pr merge 1)'` and `"run \\`gh pr merge 5\\` later"` write
    about a merge, and `echo "$(gh pr merge 1)"` is one. What a substitution runs is read as a
    line of its own, so `"Merged at $(date). Next: gh pr merge 6"` runs `date`.
    """
    text, documents = _read(_without_substituted_documents(line))
    expanded = [(text, True), *((document, False) for opened, document in documents
                                if not _delimited_by_a_quote(opened))]
    try:
        return any(_runs_text(inner, wanted)
                   for body, quoting in expanded for inner in substitutions(body, quoting))
    except RecursionError:
        # Nested past what a stack can follow: read as running it, never as running nothing.
        return True


def _delimited_by_a_quote(heredoc: str) -> bool:
    """`<<'EOF'`, `<<"EOF"` and `<<\\EOF`, whose document bash expands nothing in."""
    return heredoc.lstrip("<").lstrip("-").strip()[:1] in ("'", '"', "\\")


# What decides where a substitution is: a single-quoted span, a backslash and what it escapes,
# `$(`, a backtick, a double quote. `$((` arithmetic is read as a substitution of `(…)`, which
# runs nothing, and a `$(` inside it is read in its turn.
_EXPANSION = re.compile(r"""(?P<single>'[^']*'?)|(?P<escape>\\.?)|(?P<opened>\$\()|(?P<tick>`)"""
                        r"""|(?P<double>")""", re.S)
_TICKED = re.compile(r"(?:\\.|[^`\\])*", re.S)


def substitutions(text: str, quoting: bool = True) -> list[str]:
    """The command each outermost `$(…)` or backtick substitution in `text` runs.

    `quoting` is False for a heredoc's document, where a quote is a character like any other.
    """
    found: list[str] = []
    at = 0
    double = False
    while True:
        hit = _EXPANSION.search(text, at)
        if hit is None:
            return found
        kind, at = hit.lastgroup, hit.end()
        if kind == "single" and (double or not quoting):
            at = hit.start() + 1
        elif kind == "opened":
            end = _closing(text, at)
            found.append(text[at:end])
            at = end + 1
        elif kind == "tick":
            end = _TICKED.match(text, at).end()
            found.append(text[at:end])
            at = end + 1
        elif kind == "double" and quoting:
            double = not double


# Inside a substitution: quoted spans and escapes, which hold no parenthesis that counts, a
# substitution or a parenthesis opened, one closed, and `case`, whose patterns close one.
_NESTED = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|\\.|\$\(|[()]|\bcase\b""", re.S)


def _closing(text: str, at: int) -> int:
    """Where the `)` closing the substitution whose command starts at `at` is: past every one
    nested in it, or the end of `text` when it has none, or when a `case` pattern's `)` could
    be taken for it."""
    depth = 1
    for hit in _NESTED.finditer(text, at):
        if hit.group() in ("$(", "("):
            depth += 1
        elif hit.group() == ")":
            depth -= 1
            if not depth:
                return hit.start()
        elif hit.group() == "case":
            break
    return len(text)


def _words(wanted: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b" + r"\s+".join(map(re.escape, wanted)) + r"\b")
