"""The review rules, in one place so a finding can be re-asked as well as raised.

Findings used to live only in `review-commit`, which raises them. Nothing could ask a
rule whether it still fires, so a finding filed by a rule that was later CORRECTED kept
blocking the merge gate forever — the `sql-interpolation` fixed in #78 was still counted
on v1.6.0, ten sightings later, over code the current rule reads as clean (#80).

A finding is a claim about code as it stands. Re-asking is one regex over one file.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_FILE_BYTES = 400_000

# Each pattern is a class that is exact, cheap, and measured to be over-produced by
# generated code. Anything requiring judgement is deliberately absent: a reviewer that
# cries wolf gets ignored, and an ignored reviewer is worse than none.
# The statement a string handed to `execute` opens with, where it is SQL at all; a bracket may
# open it, as it opens a union.
_SQL_VERB = (r"\s*\(?\s*(?:select|insert|update|delete|with|create|alter|drop|merge|replace|"
             r"truncate|explain|copy|set|show|call|values|grant|revoke|vacuum|analyze|refresh|"
             r"comment|lock|notify)\b")

CHECKS: list[tuple[str, re.Pattern[str], str]] = [
    ("swallowed-exception", re.compile(r"except[^:\n]*:\s*(?:#[^\n]*)?\n\s*pass\b"),
     "exception swallowed silently — the highest-prevalence measured regression in generated code"),
    ("bare-except", re.compile(r"except\s*:\s*\n"),
     "bare except catches KeyboardInterrupt and SystemExit too"),
    ("shell-injection", re.compile(r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True"),
     "shell=True with a constructed command"),
    # `%s` inside a plain string literal is psycopg's PLACEHOLDER, and the parameters ride
    # in the next argument — it is the form people are told to use INSTEAD of interpolation,
    # and the old pattern flagged it (#75). A reviewer that flags the safe form teaches its
    # reader to ignore it, and the next finding, a real one, is ignored too.
    #
    # So interpolation is what it says: an f-string with a placeholder, or a literal handed
    # to `%` or `+`. A literal followed by a comma is a parameter binding and is left alone.
    #
    # And it is SQL: the string starts with a statement. Any call whose name ended in `query`
    # matched whatever it was handed, so fuddy's assistant splitting a product's name and
    # composition into words, `split_query(f"{name} {composition}")`, was reported as SQL (on
    # 2.7.1). A wrapper's name still counts: `db_execute(f"SELECT … {x}")` is the real thing.
    # A string is read to the quote that opened it, on its line, so the quotes of SQL inside are
    # its text: `"… ILIKE '%%москв%%'"` was SQL handed to `%`. A triple-quoted one is not read,
    # its second quote opening no statement, as 2.11 read none: reading it named four trusted
    # fragments (`{what}`, `{_visible()}`) and one plain string in fuddy's last 300 commits, and
    # no input.
    ("sql-interpolation", re.compile(
        r"(?i)(?:execute|executemany|query)\s*\(\s*(?:"
        r"f(?P<f>[\"'])" + _SQL_VERB + r"(?:(?!(?P=f))[^\n])*?\{"
        r"|(?P<q>[\"'])" + _SQL_VERB + r"(?:(?!(?P=q))[^\n])*(?P=q)\s*(?:%|\+)"
        r")"),
     "SQL built by string interpolation"),
    ("disabled-verification", re.compile(r"(?i)verify\s*=\s*False|rejectUnauthorized\s*:\s*false"),
     "certificate verification disabled"),
    # `skip`, never `skipif` with a condition: that is a guard on the environment a test needs,
    # not a test switched off. A new test carrying `skipif(not _db_ok(), reason="требуется
    # Postgres")`, which ran in CI and skipped nothing more than before, was flagged and kept
    # its pull request from merging after the founder's `+merge` (#283). A condition that is
    # always true switches the test off, and is still one. A bare `fit(` or `xdescribe(` is a
    # test only with the description every runner's test call opens with: fuddy's landing has a
    # `function fit()` and its meal planner calls `fit(day.kcal, eaten?.kcal)`.
    ("skipped-test", re.compile(
        r"(?i)@pytest\.mark\.skip\b|@pytest\.mark\.skipif\(\s*(?:True|1)\s*[,)]"
        r"|@pytest\.mark\.xfail\b(?!\()|@unittest\.(?:skip|expectedFailure)\b(?!If|Unless)"
        r"|\b(?:it|test|describe)\.(?:skip|only)\(|(?<![.\w])[xf](?:it|describe)\(\s*['\"`]|\bt\.Skip\("
        r"|#\[ignore\]|@(?:Disabled|Ignore)\b"),
     "test skipped or narrowed — freeze the scoring rules, do not edit them"),
    ("debug-leftover", re.compile(r"(?:^|\s)(?:breakpoint\(\)|debugger;|console\.log\()"),
     "debugging leftover"),
    # The moves that make a red suite read green without touching the code it tests, as
    # isitdone publishes them (102 of 103 caught on its corpus, none of 89 honest changes
    # flagged) and as arXiv 2610.12822 found them passed between agents: a test command that
    # cannot fail, an object equal to everything, an exit the test chooses, a report rewritten
    # in a pytest hook (decision 0074). Each is a shape no honest test needs.
    ("defused-test-command", re.compile(
        r"(?im)\b(?:pytest|jest|vitest|mocha|go\s+test|cargo\s+test|(?:npm|yarn|pnpm)\s+(?:run\s+)?test"
        r"|make\s+test|unittest|rspec|phpunit)\b[^\n]*?\|\|\s*(?:true|exit\s+0|:)(?![\w-])"
        r"|--passWithNoTests\b|(?:^|\s)-DskipTests\b|\bignoreFailures\s*=\s*true\b"
        r"|^\s*addopts\s*=[^\n]*(?:--deselect\b|-p\s+no:|-k\s+[\"']?not\b|--ignore(?:-glob)?=\S*test)"
        r"|\"test\"\s*:\s*\"(?:true|exit 0)\""),
     "a test command that cannot fail — it reports a pass whatever the tests do"),
    ("always-equal", re.compile(
        r"def\s+__eq__\s*\([^)]*\)\s*(?:->\s*[\w\[\], .]+)?:\s*(?:#[^\n]*)?\n\s+return\s+True\b"),
     "`__eq__` that answers True to anything — every equality assertion on it passes"),
    ("exit-in-test", re.compile(
        r"\bos\._exit\s*\(|\bsys\.exit\s*\(\s*(?:0|True|None)?\s*\)|\bos\.Exit\(\s*0\s*\)"
        r"|\bprocess\.exit\(\s*0?\s*\)"),
     "a test that exits the process — the run ends with the code the test chose"),
    ("outcome-rewritten", re.compile(
        r"\.outcome\s*=\s*[\"']passed[\"']|\.force_result\s*\(|\.force_exception\s*\("),
     "a test hook that rewrites a result — a failure reads as a pass"),
    ("tautology", re.compile(
        r"(?m)^\s*assert\s+(?:True|1|1\s*==\s*1|not\s+False)\s*(?:,[^\n]*)?$"
        r"|\bassertTrue\(\s*True\s*\)|\bassertEqual\(\s*(?P<a>[\w.]+)\s*,\s*(?P=a)\s*\)"
        r"|\bexpect\(\s*(?P<b>[\w.]+)\s*\)\.toBe\(\s*(?P=b)\s*\)"),
     "an assertion that cannot fail"),
    ("early-return-in-test", re.compile(
        r"(?m)^\s*def\s+test_\w*\s*\([^)]*\)\s*(?:->\s*None\s*)?:\s*\n"
        r"(?:\s*(?:\"\"\"[^\n]*\"\"\"|'''[^\n]*''')\s*\n)?\s+return\s*(?:None\s*)?$"
        r"|^\s*func\s+Test\w*\(\s*\w+\s+\*testing\.T\s*\)\s*\{\s*\n\s*return\s*\n"),
     "a test that returns before it asserts anything"),
]

# Where a check reads at all: those that are only tampering inside a test. `sys.exit(0)` is how a
# command-line program ends, and a test is the one place it decides what a run reports.
TEST_FILE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?)/|(?:^|/)test_[^/]*\.py$|_test\.(?:py|go)$"
    r"|\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)conftest\.py$")
IN_TESTS_ONLY = frozenset({"exit-in-test", "outcome-rewritten", "tautology", "early-return-in-test"})


def reads(detector: str, relative: str) -> bool:
    """Does `detector` read this file at all? Every check reads every file but the test-only ones."""
    return detector not in IN_TESTS_ONLY or bool(TEST_FILE.search(relative.replace("\\", "/")))


_BY_NAME = {name: pattern for name, pattern, _ in CHECKS}


def still_fires(root: Path, detector: str, relative: str) -> bool:
    """Does `detector` still find something in `relative`, as the file stands now?

    True for anything this cannot answer — an unknown detector, an unreadable file, a path
    that has moved. Retiring a finding nobody can re-check would be deciding it is false on
    the strength of not knowing, which is the failure this project is written against.
    """
    try:
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    if len(text) > MAX_FILE_BYTES:
        return True

    if detector == "secret":
        from . import redact

        return bool(redact.find(text))
    pattern = _BY_NAME.get(detector)
    if pattern is None:
        return True
    return reads(detector, relative) and bool(pattern.search(text))
