#!/usr/bin/env python3
"""The same defect classes as the Python gates, for the languages a founder actually ships.

`check_slop.py` and `check_docstrings.py` are AST-exact and Python-only. That covered
nothing for someone building a Next.js frontend against a Go API, which is the normal
shape of the products this plugin exists for — so the strongest quality gates applied to
the smallest part of the codebase.

This is regex-based and says so. An AST per language would mean a parser per language,
and the hard constraint here is standard library only: no tree-sitter, no typescript
compiler, no dependency that runs on every check. The trade is real and it is the right
way round — a pattern that catches `catch (e) {}` in every JS file beats a perfect parser
for one language nobody here writes in.

Everything flagged is a defect class with a PERMANENT budget of zero. Nothing is
ratcheted, because unlike complexity these are not gradual: an empty catch block is
wrong on the day it is written, and there is no legacy allowance that makes it less wrong.

Exit 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Sibling module; `tools/` is on sys.path because these run as scripts from there.
import _scope

CHECKED_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt"}

SKIP_PARTS = {
    ".git", "node_modules", "dist", "build", "target", "vendor", ".next", "out",
    "coverage", "__pycache__", ".venv", "generated",
}

MAX_FILE_BYTES = 400_000


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    why: str
    suffixes: frozenset


def _rule(name: str, pattern: str, why: str, suffixes: set) -> Rule:
    return Rule(name, re.compile(pattern), why, frozenset(suffixes))


_JS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_TS = {".ts", ".tsx"}
_ALL = CHECKED_SUFFIXES

RULES: list[Rule] = [
    _rule(
        "swallowed-exception",
        r"catch\s*(?:\([^)]*\))?\s*\{\s*\}|\.catch\s*\(\s*\(?\s*\w*\s*\)?\s*=>\s*\{\s*\}\s*\)",
        "an empty catch turns a failure into wrong behaviour with no signal",
        _JS,
    ),
    _rule(
        "ignored-error",
        r"(?m)^\s*(?:_\s*[,)]?\s*=\s*\w+|if\s+err\s*!=\s*nil\s*\{\s*\})",
        "discarding an error in Go is the same failure with different syntax",
        {".go"},
    ),
    _rule(
        "unwrap-in-production",
        r"\.unwrap\(\)|\.expect\(",
        "unwrap panics on the case you did not think about; handle or propagate",
        {".rs"},
    ),
    _rule(
        "any-type",
        r":\s*any\b(?!\s*\/\/\s*deliberate)|<any>|as\s+any\b",
        "`any` switches off the checking the language is for; use unknown and narrow",
        _TS,
    ),
    _rule(
        "derivable-jsdoc",
        r"@(?:param|returns?|type)\s*\{",
        "the type annotation already says this; a JSDoc type is a second copy that rots",
        _JS,
    ),
    _rule(
        "left-in-console",
        r"(?m)^\s*console\.(?:log|debug|dir)\s*\(",
        "debugging output shipped to users; use the project logger or delete it",
        _JS,
    ),
    _rule(
        "narration-comment",
        r"(?im)^\s*//\s*(?:step\s*\d|\d\)\s|first,|then,|next,|finally,|now\s+we\b)",
        "narrating the next line adds nothing the line does not say",
        _ALL,
    ),
    _rule(
        "commented-out-code",
        r"(?m)^(?:\s*//\s*(?:if|for|while|function|const|let|var|return|class)\b.*\n){3,}",
        "commented-out code is answered by git history, and it rots silently",
        _ALL,
    ),
    _rule(
        "bare-todo",
        r"//\s*(?:TODO|FIXME|XXX|HACK)\b(?!\s*[(\[])",
        "a TODO naming no owner and no ticket will never be seen again",
        _ALL,
    ),
]


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    why: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.why}\n      {self.text[:100]}"


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30
    )
    return Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()


ROOT = repo_root()


def changed_files() -> list[Path]:
    """Only what this turn touched, so the gate is usable in an existing codebase."""
    seen: set[str] = set()
    for args in (["diff", "--name-only", "HEAD"], ["diff", "--name-only", "--cached"],
                 ["ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        seen.update(p for p in proc.stdout.splitlines() if p)
    return [ROOT / rel for rel in sorted(seen)]


def all_files() -> list[Path]:
    # Same reason as `check_slop`: a nested worktree is this repository again, and
    # this fallback runs exactly when git could not narrow the list itself.
    nested = _scope.nested_worktrees(ROOT)
    return [p for p in ROOT.rglob("*") if p.is_file() and not _scope.is_inside(p, nested)]


def eligible(path: Path) -> bool:
    if path.suffix not in CHECKED_SUFFIXES or not path.is_file():
        return False
    if SKIP_PARTS & set(path.parts):
        return False
    if re.search(r"\.(?:min|bundle|generated|d)\.", path.name):
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def strip_strings(source: str) -> str:
    """Blank out string literals so a rule never fires on text that merely mentions it.

    Length-preserving, so reported line numbers stay correct — a finding at the wrong
    line is a finding the reader stops trusting.
    """
    def blank(match: re.Match) -> str:
        return match.group(0)[0] + " " * (len(match.group(0)) - 2) + match.group(0)[-1]

    return re.sub(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`", blank, source)


def scan(path: Path) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = path.relative_to(ROOT).as_posix()
    scannable = strip_strings(source)
    lines = source.splitlines()
    out: list[Finding] = []

    for rule in RULES:
        if path.suffix not in rule.suffixes:
            continue
        for match in rule.pattern.finditer(scannable):
            number = scannable[: match.start()].count("\n") + 1
            text = lines[number - 1].strip() if number <= len(lines) else ""
            out.append(Finding(rel, number, rule.name, rule.why, text))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality gates for non-Python source.")
    parser.add_argument("--all", action="store_true", help="scan the whole tree, not the diff")
    args = parser.parse_args()

    paths = [p for p in (all_files() if args.all else changed_files()) if eligible(p)]
    findings = [f for path in paths for f in scan(path)]

    if not paths:
        # "clean — 0 files checked" is the exact shape of lie this repository exists to
        # refuse: a green line printed over an empty set. This repo is pure Python, so
        # zero is the correct answer here — but it is a NOT-RUN, and it has to read as one.
        print("polyglot: NOT RUN — no non-Python source in scope (rules exercised by tests/test_polyglot.py)")
        return 0

    if not findings:
        print(f"polyglot: clean — {len(paths)} non-Python file(s) checked")
        return 0

    print(f"polyglot: {len(findings)} finding(s) across {len(paths)} file(s)")
    for finding in findings[:40]:
        print(f"  {finding}")
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    print("\nEvery class here has a permanent budget of zero — none of them gets better with age.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
