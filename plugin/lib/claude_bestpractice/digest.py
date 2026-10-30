"""What a long successful command result is worth to whoever reads it next.

Claude Code hands the model a successful command's output whole up to 30,000 characters,
and past that a file path and the first 2,000 characters (measured on 2.1.303: `seq 1 30000`
reached the model as a `<persisted-output>` block holding 2 KB). A runner puts its verdict
LAST, so a verbose suite that passed reached the model as a page of `PASSED` and no answer.
One user counted a month of it: the saved file was opened 36 times out of 172, and the other
136 times the model went on from the head (picklog.cc, September 2026).

Under the threshold the cost is the window: Fuddy's `jest --ci --verbose` printed 8.9 MB,
and a passing `pytest -v` of a few hundred tests is tens of kilobytes of lines that each say
one test passed — every one of them re-read on every later turn until a compaction throws
them, and everything around them, away.

So a digest keeps what a reader acts on — the runner's own summary, every line that looks
like an error or a warning, the head and the tail, the identifiers in what it leaves out —
and the header says where all of it is. It is built only for what runs test suites,
installs and builds, whose successful output is noise, and for any output Claude Code was
going to cut to its first 2 KB anyway. Output a session asked to read — a file, a diff, a
search — is never touched, and a failed command is left as Claude Code keeps it: the middle
of a failure is already gone by the time anything here could see it, and a digest of what
is left would only reorder it.
"""

from __future__ import annotations

import re

from . import shellcmd

# The classes this module tells apart.
TESTS = "tests"
BUILD = "build"
OTHER = "other"
READ = "read"

# Below this a successful result costs less to read than a digest of it saves (about 1,700
# tokens of English, fewer of Russian).
MIN_CHARS = 6_000
# What a digest may cost before its header. Kept under the 2,000-character preview Claude
# Code itself would have shown for a persisted result plus what that preview never had.
BODY_CHARS = 2_400
# A digest that does not save this share of the cleaned output keeps too little to be worth
# what it drops.
MIN_SAVING = 0.4
# One kept line. A minified bundle or a JSON blob on one line is the shape this catches.
LINE_CHARS = 300
TRUNCATED = " […line truncated]"

# What a session asked to SEE — a file, a diff, a match, a listing. However long it is, the
# content is the answer, so a line that runs one of these is never condensed.
_READERS = frozenset({
    "cat", "bat", "batcat", "less", "more", "head", "tail", "nl", "xxd", "od", "hexdump",
    "jq", "yq", "diff", "grep", "egrep", "fgrep", "rg", "ag", "ack", "find", "fd", "ls",
    "tree", "wc", "sort", "uniq", "cut", "awk", "sed", "column", "file", "stat", "env",
    "printenv", "psql", "sqlite3", "curl", "wget",
})
_GIT_READS = frozenset({
    "show", "diff", "log", "blame", "grep", "ls-files", "ls-tree", "cat-file", "status",
    "reflog", "shortlog", "rev-list", "for-each-ref", "branch", "tag", "stash", "worktree",
})

_TEST_PROGRAMS = frozenset({
    "pytest", "py.test", "jest", "vitest", "mocha", "ava", "rspec", "phpunit", "karma",
    "playwright", "unittest", "nose2", "tox", "nox",
})
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_INSTALLS = frozenset({
    "install", "i", "ci", "add", "update", "upgrade", "up", "remove", "rm", "uninstall",
    "dedupe", "prune", "rebuild", "link",
})
_TEST_NAME = re.compile(r"^(?:test|tests|check|ci|cov|coverage|verify|e2e)(?:[-:_.].*)?$")
_BUILD_NAME = re.compile(r"^(?:build|install|deps|setup|bootstrap|compile|bundle|dist|prepare)(?:[-:_.].*)?$")

# (program, first word) → class, for programs whose class turns on their subcommand.
# `None` as the first word means every subcommand.
_SUBCOMMANDS: dict[tuple[str, str | None], str] = {
    ("go", "test"): TESTS, ("go", "build"): BUILD, ("go", "install"): BUILD,
    ("go", "mod"): BUILD, ("go", "generate"): BUILD,
    ("cargo", "test"): TESTS, ("cargo", "nextest"): TESTS, ("cargo", "build"): BUILD,
    ("cargo", "check"): BUILD, ("cargo", "install"): BUILD, ("cargo", "fetch"): BUILD,
    ("dotnet", "test"): TESTS, ("dotnet", "build"): BUILD, ("dotnet", "restore"): BUILD,
    ("deno", "test"): TESTS, ("swift", "test"): TESTS, ("swift", "build"): BUILD,
    ("node", "--test"): TESTS, ("mvn", "test"): TESTS, ("mvn", "verify"): TESTS,
    ("pip", "install"): BUILD, ("pip3", "install"): BUILD, ("uv", "sync"): BUILD,
    ("uv", "pip"): BUILD, ("uv", "add"): BUILD, ("uv", "lock"): BUILD,
    ("poetry", "install"): BUILD, ("poetry", "add"): BUILD, ("poetry", "update"): BUILD,
    ("poetry", "lock"): BUILD, ("pipenv", "install"): BUILD, ("pdm", "install"): BUILD,
    ("docker", "build"): BUILD, ("docker", "pull"): BUILD, ("docker", "push"): BUILD,
    ("docker-compose", "build"): BUILD, ("docker-compose", "pull"): BUILD,
    ("eas", "build"): BUILD, ("eas", "update"): BUILD, ("eas", "submit"): BUILD,
    ("expo", "export"): BUILD, ("expo", "prebuild"): BUILD, ("expo", "install"): BUILD,
    ("pod", "install"): BUILD, ("brew", "install"): BUILD, ("apt", "install"): BUILD,
    ("apt-get", "install"): BUILD, ("bundle", "install"): BUILD, ("composer", "install"): BUILD,
    ("git", "clone"): BUILD, ("git", "fetch"): BUILD, ("git", "pull"): BUILD,
    ("git", "push"): BUILD, ("vite", "build"): BUILD, ("next", "build"): BUILD,
    ("tsc", None): BUILD, ("webpack", None): BUILD, ("gradle", None): BUILD,
    ("gradlew", None): BUILD, ("mvn", None): BUILD, ("xcodebuild", None): BUILD,
    ("rollup", None): BUILD, ("esbuild", None): BUILD,
}

# Launchers that run the program named after them: `npx jest` is jest, `python -m pytest`
# is pytest, `uv run pytest` is pytest.
_LAUNCHERS = frozenset({"npx", "bunx", "pnpx"})
_RUNNERS = frozenset({
    ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"),
    ("rye", "run"), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx"), ("bundle", "exec"),
})


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _program(argv: list[str]) -> list[str]:
    """argv with any launcher in front of the real program taken off."""
    for _ in range(4):
        inner = _unlaunched(argv)
        if inner is argv:
            return argv
        argv = inner
    return argv


def _unlaunched(argv: list[str]) -> list[str]:
    """argv without the one launcher in front of it, or argv itself when none is."""
    name = _base(argv[0]) if argv else ""
    if name in _LAUNCHERS:
        rest = argv[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return rest
    if len(argv) > 2 and ((name, argv[1]) in _RUNNERS
                          or (name.startswith("python") and argv[1] == "-m")):
        return argv[2:]
    return argv


def _named_script(name: str) -> str | None:
    if _TEST_NAME.match(name):
        return TESTS
    if _BUILD_NAME.match(name):
        return BUILD
    return None


def _package_script(rest: list[str]) -> str | None:
    """`npm test`, `npm run build`, `yarn test:unit`, `pnpm install`."""
    verb = rest[0] if rest else ""
    if verb in _INSTALLS:
        return BUILD
    if verb in ("run", "run-script") and len(rest) > 1:
        return _named_script(rest[1])
    return _named_script(verb)


def _make_targets(rest: list[str]) -> str | None:
    """`make test` is a suite, `make build` a build, and anything else is the project's own."""
    targets = _targets(rest)
    if any(_TEST_NAME.match(target) for target in targets):
        return TESTS
    if targets and all(_BUILD_NAME.match(target) for target in targets):
        return BUILD
    return None


def _targets(rest: list[str]) -> list[str]:
    """The targets a `make` line names, past its flags and the values they take."""
    targets: list[str] = []
    skip = False
    for word in rest:
        if skip:
            skip = False
        elif word in ("-C", "-f", "--directory", "--file"):
            skip = True
        elif not word.startswith("-") and "=" not in word:
            targets.append(word)
    return targets


def _reads(argv: list[str]) -> bool:
    name = _base(argv[0])
    if name in _READERS:
        return True
    if name != "git":
        return False
    words = [word for word in argv[1:] if not word.startswith("-")]
    return bool(words) and words[0] in _GIT_READS


def _kind_of(argv: list[str]) -> str | None:
    argv = _program(argv)
    if not argv:
        return None
    name, rest = _base(argv[0]), argv[1:]
    if name in _TEST_PROGRAMS:
        return TESTS
    if name in _PACKAGE_MANAGERS:
        return _package_script(rest)
    if name == "make":
        return _make_targets(rest)
    first = rest[0] if rest else ""
    return _SUBCOMMANDS.get((name, first)) or _SUBCOMMANDS.get((name, None))


def classify(line: str, declared: tuple[tuple[str, ...], ...] = ()) -> str:
    """What `line` runs: TESTS, BUILD, READ (never condensed) or OTHER.

    `declared` is the project's own test commands, which count as a suite whatever their
    names. A line that cannot be read is READ: what it does is not known, so it is not ours
    to shorten.
    """
    found = shellcmd.commands(line)
    if not found or any(_reads(argv) for argv in found):
        return READ
    if any(tuple(argv) in declared for argv in found):
        return TESTS
    kinds = {_kind_of(argv) for argv in found}
    if TESTS in kinds:
        return TESTS
    if BUILD in kinds:
        return BUILD
    return OTHER


# ---------------------------------------------------------------------------------------------
# Cleaning. Lossless for a reader: colours, a progress bar's earlier frames and the exact
# repetition of a line carry nothing the last frame and a count do not.

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def clean(text: str) -> str:
    """`text` without terminal colours, with each redrawn line at its last frame and every
    run of three or more identical lines folded into one with its count."""
    lines = []
    for raw in _ANSI.sub("", text).split("\n"):
        line = raw.rstrip("\r")
        lines.append(line.rsplit("\r", 1)[-1].rstrip())
    return "\n".join(_folded(lines))


def _folded(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        count = j - i + 1
        out.extend([f"{lines[i]}  [×{count}]"] if count >= 3 else lines[i:j + 1])
        i = j + 1
    return out


# ---------------------------------------------------------------------------------------------
# What a reader acts on.

# Whole words and the shapes runners use, never a bare `E[A-Z]+`: a pattern that matched
# "Entry" crowded the real errors out of one tool's twenty-line cap. Russian forms because
# a WSL machine with a ru_RU locale prints Postgres and git errors in Russian, where
# Postgres says ERROR as `ОШИБКА:`, FATAL as `ВАЖНО:` and PANIC as `ПАНИКА:`.
_ERRORISH = re.compile(
    r"\b(?:ERROR|ERR!|FAIL(?:ED|URE|S)?|FATAL|PANIC|Traceback|panicked|fatal:|error(?:\[\w+\])?:"
    r"|Error:|npm error)|(?:ОШИБКА|ВАЖНО|ПАНИКА|ошибка):"
    r"|\b[A-Z]\w*(?:Error|Exception)\b|[✗✕✘]|^\s*●\s"
)
_WARNISH = re.compile(r"\b(?:WARN(?:ING)?|Warning|DeprecationWarning|deprecated|ПРЕДУПРЕЖДЕНИЕ)\b")
# The runner's verdict, however it phrases it.
_SUMMARY = re.compile(
    r"(?<![\w.])\d+\s+(?:passed|failed|errors?|xpassed|xfailed|skipped|deselected|warnings?"
    r"|passing|failing|pending|todo|total)\b"
    r"|^Ran \d+ tests?\b|^(?:OK|FAILED)(?:\s*\(.*\))?$|^Tests?:\s|^Test Suites:|^Test Files\s"
    r"|^Snapshots:|^Time:\s|^test result:|^(?:ok|FAIL|PASS)\s+\S+\s+[\d.]+m?s\b"
    r"|^[#ℹ]\s*(?:tests|pass|fail)\s+\d+|^=+ .* in [\d.]+s|^(?:added|removed|changed|up to date)\b"
    r"|^Successfully (?:installed|built)\b|^BUILD (?:SUCCESSFUL|FAILED)\b|\bdone in [\d.]+s\b",
    re.I,
)
# A line that only says one test passed, or that a progress bar moved.
_PASSING = re.compile(
    r"^\s*(?:[✓✔√]\s|ok\s+\d+\b|PASS(?:ED)?\b)|\sPASSED\b|^\s*\S+\.py\s+[.sx]+\s*(?:\[\s*\d+%\])?$"
    r"|^[.sx]+\s*(?:\[\s*\d+%\])?$"
)
# A pytest node id. On such a line a word like ERROR is usually part of a test's name or its
# parameters (`[error0-ERROR]`), and `TestCheeseException` is a class of tests, not a raise:
# only the runner's own verdict beside the id says something broke.
_NODE_ID = re.compile(r"\S+\.py::\S")
_NODE_VERDICT = re.compile(r"(?:^|\]\s+)(?:FAILED|ERROR)\s|\s(?:FAILED|ERROR)(?:\s+\[|\s*$)")
_IDENTIFIER = re.compile(
    r"https?://[^\s'\"<>)\]]+"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)(?:[0-9a-f]{40}|[0-9a-f]{7,12})\b"
    r"|\bv?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b"
)
IDENTIFIERS_KEPT = 16

# What a line is, worked out once: a failed pytest run is twenty-four thousand lines, and
# asking every question again for every budget took three and a half seconds.
_ERR, _WARN, _SUM, _PASS = 1, 2, 4, 8

# (head, tail, error context, errors kept, warnings kept), widest first. A digest takes the
# widest that fits its budget.
_SHAPES = ((5, 20, 2, 30, 8), (3, 12, 1, 20, 5), (2, 8, 0, 12, 3), (1, 5, 0, 8, 2))
# A gap this small costs no more to show than to describe.
_SHOWN_GAP_LINES = 2
_SHOWN_GAP_CHARS = 200


def _flag(line: str) -> int:
    if _PASSING.search(line):
        return _PASS
    if _NODE_ID.search(line):
        return _ERR if _NODE_VERDICT.search(line) else 0
    flags = _SUM if _SUMMARY.search(line) else 0
    if _ERRORISH.search(line):
        return flags | _ERR
    return flags | (_WARN if _WARNISH.search(line) else 0)


def _distinct_warnings(lines: list[str], flags: list[int]) -> list[int]:
    """The first line of each distinct warning, digits aside."""
    seen: set[str] = set()
    kept: list[int] = []
    for i, flag in enumerate(flags):
        key = re.sub(r"\d+", "#", lines[i].strip()) if flag & _WARN else ""
        if key and key not in seen:
            seen.add(key)
            kept.append(i)
    return kept


def _choose(flags: list[int], marked: list[int], warnings: list[int], shape: tuple[int, ...]) -> set[int]:
    head, tail, context, error_limit, warning_limit = shape
    total = len(flags)
    chosen = set(range(min(head, total))) | set(range(max(0, total - tail), total))
    chosen.update(i for i, flag in enumerate(flags) if flag & _SUM)
    for i in [i for i, flag in enumerate(flags) if flag & _ERR][:error_limit]:
        chosen.update(range(max(0, i - context), min(total, i + context + 1)))
    chosen.update(warnings[:warning_limit])
    chosen.update(marked)
    return chosen


def _clip(line: str) -> str:
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + TRUNCATED


def _gap(lines: list[str], flags: list[int]) -> list[str]:
    """What stands for lines left out: the lines themselves when that is as short, otherwise
    one note saying how many there were and how many of them were a test passing."""
    words = [line for line in lines if line.strip()]
    if not words:
        return []
    if len(words) <= _SHOWN_GAP_LINES and sum(map(len, words)) <= _SHOWN_GAP_CHARS:
        return [_clip(line) for line in words]
    passing = sum(1 for flag in flags if flag & _PASS)
    noun = "line" if len(words) == 1 else "lines"
    detail = f", {passing} of them a test passing" if passing else ""
    return [f"[… {len(words)} {noun}{detail} …]"]


def _render(lines: list[str], flags: list[int], chosen: set[int]) -> tuple[list[str], list[str]]:
    """The kept lines in order with what stands for each gap, and the lines no one will see."""
    out: list[str] = []
    dropped: list[str] = []
    start = 0
    for i in sorted(chosen) + [len(lines)]:
        if i > start:
            standing = _gap(lines[start:i], flags[start:i])
            out.extend(standing)
            if standing and standing[0].startswith("[… "):
                dropped.extend(lines[start:i])
        if i < len(lines):
            out.append(_clip(lines[i]))
        start = i + 1
    return out, dropped


def identifiers(lines: list[str]) -> list[str]:
    """Commit hashes, versions, UUIDs and URLs in `lines`, first seen first, up to sixteen.

    What a session needs to act on output it did not read: the build or update id to roll
    back, the URL to open, the revision to check out.
    """
    seen: dict[str, None] = {}
    for line in lines:
        for found in _IDENTIFIER.findall(line):
            seen.setdefault(found.rstrip(".,;:"), None)
            if len(seen) >= IDENTIFIERS_KEPT:
                return list(seen)
    return list(seen)


def _attempt(lines: list[str], flags: list[int], picks: tuple[list[int], list[int]], shape: tuple[int, ...], kind: str) -> str:
    marked, warnings = picks
    out, dropped = _render(lines, flags, _choose(flags, marked, warnings, shape))
    errors = sum(1 for flag in flags if flag & _ERR)
    if errors > shape[3]:
        out.append(f"[{errors - shape[3]} more lines that look like errors are in the full output]")
    ids = identifiers(dropped) if kind != TESTS else []
    if ids:
        out.append("[left out, but named there: " + " ".join(ids) + "]")
    return "\n".join(out)


def condense(text: str, kind: str, touched: tuple[str, ...] = (), budget: int = BODY_CHARS) -> str | None:
    """`text` cut to what its reader acts on, or None when that saves too little to be worth it.

    `touched` names files this session changed: a line naming one is kept, since a session
    that just wrote a test wants to see it ran.
    """
    lines = clean(text).split("\n")
    flags = [_flag(line) for line in lines]
    marked = [i for i, line in enumerate(lines) if touched and any(path in line for path in touched)][:20]
    picks = (marked, _distinct_warnings(lines, flags))
    body = ""
    for shape in _SHAPES:
        body = _attempt(lines, flags, picks, shape, kind)
        if len(body) <= budget:
            break
    if len(body) > budget:
        body = body[:budget].rsplit("\n", 1)[0] + "\n[… the digest itself was cut at its size]"
    if len(body) > (1 - MIN_SAVING) * len(text):
        return None
    return body
