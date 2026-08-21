"""How many tests the SOURCE TREE contains, counted by this gate rather than reported.

Six rounds of adversarial verification defeated the evidence gate the same way each time,
one level lower every round:

    round 1   it trusted an artifact file          hand-written XML
    round 4   it trusted the exit code             `-` before a Makefile recipe
    round 5   it trusted the words "N failed"      stop printing them
    round 6   it trusted the count "N passed"      `@echo '2 passed in 0.03s'`

The pattern is not a sequence of oversights, it is one mistake made four times: every
signal above is written to stdout by a process whose command, whose recipe, and whose
source the agent controls. Reading that stream harder cannot fix it. The cheapest known
forgery is now a single shell word, and there is no fifth level that is any safer.

So this module counts tests the one way the gated party does not author: by reading the
test files and counting test declarations. It is not a runner and never executes
anything. Its only claim is arithmetic — "this tree declares 47 tests" — and the gate
compares that against what the run said it executed.

That is not a proof of correctness and is not sold as one. It closes the specific gap
that a run reporting far fewer tests than exist, or a tree that shed its tests between a
red record and a green one, now has to survive a comparison against a number the agent
would have to write real test declarations to move. Deleting the failing test and echoing
a passing summary both become visible; writing forty genuine tests to hide one failure is
a cost this plugin is content to impose.

Deliberately structural and deliberately dumb: regex over declarations, no imports, no
execution, no parsing of anything that could run. A wrong count costs a warning, and
executing a repository's code to count its tests would be a far worse trade.
"""

from __future__ import annotations

import re
from pathlib import Path

# One pattern per ecosystem, matched against declarations rather than call sites. Each is
# anchored to the start of a line (allowing indentation) so that a string mentioning
# "def test_" inside a docstring does not inflate the count.
_PATTERNS: list[tuple[tuple[str, ...], re.Pattern]] = [
    ((".py",), re.compile(r"(?m)^\s*(?:async\s+)?def\s+test\w*\s*\(")),
    ((".go",), re.compile(r"(?m)^\s*func\s+(?:Test|Fuzz|Benchmark)\w*\s*\(")),
    ((".rs",), re.compile(r"(?m)^\s*#\[(?:test|tokio::test|rstest)\]")),
    ((".java", ".kt", ".kts"), re.compile(r"(?m)^\s*@Test\b")),
    ((".rb",), re.compile(r"(?m)^\s*(?:it|specify)\s+['\"]|^\s*def\s+test_\w+")),
    ((".php",), re.compile(r"(?m)^\s*public\s+function\s+test\w*\s*\(")),
    ((".cs",), re.compile(r"(?m)^\s*\[(?:Test|Fact|Theory)\]")),
    (
        (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        # `it.each`, `test.concurrent` and `it.only` are still one declaration each;
        # `describe` is a grouping and is deliberately not counted.
        re.compile(r"(?m)^\s*(?:it|test)(?:\.\w+)*\s*(?:\(|`)"),
    ),
]

# Where tests live, by convention across the ecosystems above. A file outside these that
# declares tests is simply not counted — undercounting is the safe direction, because the
# count is used as a FLOOR that a run must not fall far below.
_TEST_FILE = re.compile(
    r"(?i)(?:^|/)(?:tests?|specs?|__tests__)/|"
    r"(?:^|/)[^/]*(?:_test|test_|\.test|\.spec|Test|Tests|Spec)[^/]*\.[a-z]+$"
)

# Languages whose tests live INSIDE the source file rather than in a parallel test tree.
# Rust's `#[test]` in a `mod tests` block at the bottom of the module is the idiom, not the
# exception, so requiring a test-shaped path would count zero tests in most Rust projects
# — and a floor of zero guards nothing.
_INLINE_TEST_SUFFIXES = {".rs"}

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "vendor", "target",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".gradle",
}

MAX_FILES = 4_000
MAX_BYTES = 400_000


def is_test_file(relpath: str) -> bool:
    return bool(_TEST_FILE.search(relpath))


def count_in_text(text: str, suffix: str) -> int:
    for suffixes, pattern in _PATTERNS:
        if suffix in suffixes:
            return len(pattern.findall(text))
    return 0


def _clean(skip: list[str] | None) -> tuple[str, ...]:
    """The exclusion list, normalised. Empty for anything that is not a usable path."""
    return tuple(
        name.strip("/") for name in (skip or [])
        if isinstance(name, str) and name.strip()
    )


def _is_skipped(rel: str, skipped: tuple[str, ...]) -> bool:
    """A path the founder excluded, or a file underneath one."""
    return any(rel == name or rel.startswith(f"{name}/") for name in skipped)


def _counted(path: Path, root: Path, skipped: tuple[str, ...]) -> str:
    """The path relative to `root` when this file's declarations count, "" when they do not.

    One decision rather than five guards in the loop: a test file, of a language we can
    count, not in a vendored directory, and not one the founder excluded.
    """
    if not path.is_file() or path.suffix not in _ALL_SUFFIXES:
        return ""
    if _SKIP_DIRS & set(path.parts):
        return ""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return ""
    if not is_test_file(rel) and path.suffix not in _INLINE_TEST_SUFFIXES:
        return ""
    return "" if _is_skipped(rel, skipped) else rel


def count_tree(root: Path, skip: list[str] | None = None) -> int:
    """Test declarations under `root`. Zero when there are none, never an exception.

    Bounded on both file count and file size: this runs inside a Stop gate, and a gate
    that takes ten seconds on a large repository is a gate the founder switches off.

    `skip` is what the founder told the gate not to run. Without it, `witness_exclude`
    defeated itself: the run really did execute fewer tests than the tree declares, so the
    guard against a NARROWED run fired and every finish came back "something narrowed the
    run — not a witnessed pass". The guard is right in general and wrong here, because the
    narrowing was authored in a file the session cannot write (#158).
    """
    skipped = _clean(skip)
    total = 0
    seen = 0
    for path in sorted(root.rglob("*")):
        if seen >= MAX_FILES:
            break
        rel = _counted(path, root, skipped)
        if not rel:
            continue
        seen += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES]
        except OSError:
            continue
        total += count_in_text(text, path.suffix)
    return total


_ALL_SUFFIXES = {suffix for suffixes, _ in _PATTERNS for suffix in suffixes}


def shortfall(declared: int, executed: int) -> float:
    """How much of the tree the run left untouched, 0.0 to 1.0.

    Zero when the run covered at least as much as the tree declares, which is the normal
    case — runners report parametrised cases individually, so `executed` routinely
    exceeds `declared` and that is not suspicious.
    """
    if declared <= 0 or executed < 0:
        return 0.0
    return max(0.0, (declared - executed) / declared)


# How far above the declared count a run may report before it stops looking like the same
# suite. Parametrisation genuinely multiplies cases — one `@pytest.mark.parametrize` over
# twenty inputs is twenty reported tests from one declaration — so the ceiling has to be
# generous. It just cannot be infinite, which is what it was.
OVERCOUNT_CEILING = 30


def plausible(declared: int, executed: int) -> bool:
    """Whether a reported count could belong to this tree, bounded on BOTH sides.

    The first version of this comparison was one-sided: it caught a run that reported far
    FEWER tests than the tree declares, and blessed any number above. The two values have
    different authors — `declared` is counted here from the files, `executed` is parsed
    from the gated party's own stdout — so a one-sided bound is an open door on the side
    it does not check. The round-six forgery `@echo '2 passed'` needed one character to
    become `@echo '9999 passed'`, and that cleared any tree under twenty thousand tests.

    This does not make the number trustworthy. It makes a forged one have to be close to
    right, which means knowing the tree, which is strictly more work than typing 9999.
    """
    if declared <= 0 or executed < 0:
        return True
    if shortfall(declared, executed) >= 0.5:
        return False
    return executed <= declared * OVERCOUNT_CEILING
