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
    ("sql-interpolation", re.compile(
        r"(?i)(?:execute|query)\s*\(\s*(?:"
        r"f[\"'][^\"']*\{"
        r"|[\"'][^\"']*[\"']\s*(?:%|\+)"
        r")"),
     "SQL built by string interpolation"),
    ("disabled-verification", re.compile(r"(?i)verify\s*=\s*False|rejectUnauthorized\s*:\s*false"),
     "certificate verification disabled"),
    ("skipped-test", re.compile(r"(?i)@pytest\.mark\.skip|\bit\.skip\(|\bxit\(|\bt\.Skip\(|\.only\("),
     "test skipped or narrowed — freeze the scoring rules, do not edit them"),
    ("debug-leftover", re.compile(r"(?:^|\s)(?:breakpoint\(\)|debugger;|console\.log\()"),
     "debugging leftover"),
]


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
    return bool(pattern.search(text))
