"""The evidence gate — the spine of the plugin.

The agent's prose is discarded. Completion is accepted only when the gate has itself
run the test suite against the code as it stands and seen it exit zero.

An earlier version accepted a machine-readable artifact that was newer than the newest
changed file. Three attacks broke it, and none needed an adversary: a hand-written
four-line JUnit file was accepted over a committed, genuinely failing test; an artifact
from a different project in 2019 was accepted; and `touch junit.xml` cleared the
freshness check on stale evidence. A file asserting that tests passed is prose with
angle brackets. So the gate stops reading assertions and runs the thing.

Why this shape and not a smarter one:

* Self-report is measured worthless. Submit rate 0.97 against a test-verified resolve
  rate of 0.65 for the strongest model in the study; two different guard prompts moved
  it by exactly zero.
* An LLM judge does not close the gap: no configuration across five judges and five
  prompt strategies beat AUROC 0.65, while a plain TF-IDF baseline hit 0.83-0.95.
  So nothing here calls a model.
* False success collapses from ~45-76% to 3% in the one benchmark domain where the
  environment verifies state independently. Same models. The verifier is the variable.

The suite runs whenever there is material change. There is deliberately no result
cache: one was tried, and every way of keying it turned out to be a way of answering
"the tests pass" without the tests having passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import provenance, store, testcount, witness
from .gitctx import GitContext, changed_files

# Consecutive Stop blocks before we stop blocking and leave a durable marker instead.
# The platform overrides the hook after 8, so escalating past that just burns turns.
MAX_CONSECUTIVE_BLOCKS = 4

CLEAN_RERUN_TIMEOUT = 300

# How much of the declared suite a run may miss before it stops counting as a witnessed
# green. Loose on purpose: parametrisation, generated cases and language-specific
# idioms all make the structural count approximate, and a false accusation here costs
# the founder a finish. Half the suite going missing is not approximation.
NARROW_RUN_SHORTFALL = 0.5


@dataclass
class Artifact:
    path: Path
    mtime: float
    passed: bool
    total: int
    failed: int
    detail: str
    skipped: int = 0
    bound: bool = False


@dataclass
class Verdict:
    ok: bool
    reason: str
    artifact: Artifact | None = None
    # Allowed, but the gate could not witness a run — no runner is detectable, so the
    # only thing available was an artifact, and an artifact is forgeable. Neither block
    # (the founder could never finish) nor pretend (that is what "no enforcement" looks
    # like): let the turn end and put it on the permanent record.
    unverified: bool = False


def find_artifacts(root: Path, globs: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in globs:
        for path in root.glob(pattern):
            if path.is_file():
                seen[path.resolve()] = None
    return sorted(seen, key=lambda p: p.stat().st_mtime, reverse=True)


def parse_artifact(path: Path) -> Artifact | None:
    """Understand JUnit XML and pytest's JSON report. Unknown formats are not evidence."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    if path.suffix == ".json":
        return _parse_pytest_json(path, mtime)
    return _parse_junit(path, mtime)


def _parse_junit(path: Path, mtime: float) -> Artifact | None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites and root.tag != "testsuites":
        return None

    total = failed = skipped = 0
    for suite in suites or [root]:
        total += int(suite.get("tests", 0) or 0)
        failed += int(suite.get("failures", 0) or 0) + int(suite.get("errors", 0) or 0)
        skipped += int(suite.get("skipped", 0) or 0)

    # Zero EXECUTED tests is not a pass. Collected-minus-skipped, not collected: one
    # `skipif` on a missing DATABASE_URL turns a whole suite into "2/2 passed" over a
    # run that asserted nothing, and that is an ordinary accident rather than an attack.
    executed = max(total - skipped, 0)
    passed = executed > 0 and failed == 0
    detail = _detail(executed, failed, skipped)
    return Artifact(path, mtime, passed, total, failed, detail, skipped)


def _detail(executed: int, failed: int, skipped: int) -> str:
    if not executed:
        return f"no tests executed ({skipped} skipped)" if skipped else "no tests collected"
    out = f"{executed - failed}/{executed} passed"
    return f"{out}, {skipped} skipped" if skipped else out


def _parse_pytest_json(path: Path, mtime: float) -> Artifact | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    total = int(summary.get("total") or summary.get("collected") or 0)
    failed = int(summary.get("failed", 0)) + int(summary.get("error", 0))
    skipped = int(summary.get("skipped", 0)) + int(summary.get("deselected", 0))
    executed = max(total - skipped, 0)
    passed = executed > 0 and failed == 0 and data.get("exitcode", 0) == 0
    return Artifact(path, mtime, passed, total, failed, _detail(executed, failed, skipped), skipped)


# A gate that tells a Node project to run pytest is a gate the agent learns to ignore.
_ARTIFACT_HINTS: list[tuple[str, str]] = [
    ("pytest.ini", "pytest --junitxml=junit.xml"),
    ("tox.ini", "pytest --junitxml=junit.xml"),
    ("Cargo.toml", "cargo nextest run --profile ci   # writes target/nextest/ci/junit.xml"),
    ("go.mod", "go test ./... 2>&1 | go-junit-report > junit.xml"),
    ("pom.xml", "mvn -q test   # surefire writes target/surefire-reports/*.xml"),
    ("build.gradle", "gradle test   # writes build/test-results/test/*.xml"),
    ("Gemfile", "bundle exec rspec --format RspecJunitFormatter --out junit.xml"),
    ("package.json", "npx vitest run --reporter=junit --outputFile=junit.xml"),
    ("pyproject.toml", "pytest --junitxml=junit.xml"),
]


def artifact_hint(ctx: GitContext) -> str:
    """How THIS project should emit a result file, inferred from what is on disk."""
    for marker, command in _ARTIFACT_HINTS:
        if (ctx.worktree_root / marker).exists():
            return command
    return "run your test suite with a JUnit XML reporter, then finish"


def newest_source_mtime(root: Path, relpaths: list[str]) -> float:
    newest = 0.0
    for rel in relpaths:
        try:
            newest = max(newest, (root / rel).stat().st_mtime)
        except OSError:
            continue
    return newest


def _is_exempt(rel: str, exempt: list[str], globs: list[str], byproducts: set[str]) -> bool:
    """Whether one changed path can be ignored. Three rules, each earned by a defect.

    PREFIX for exempt paths, and never a component match: applying the component rule to
    every exempt entry made `app/reports/generator.py` and anything under a directory
    called docs/ or target/ invisible, so a red suite in ordinary domain code finished
    silently green.

    A byproduct directory never hides SOURCE. `coverage/` is a report directory in most
    repositories and a package in some, `reports/` is a service in plenty, and only the
    extension separates them — a coverage report is not written in Python. The rule leans
    the safe way: a stray source file inside a real byproduct directory costs one
    unnecessary suite run, while the reverse never runs the suite at all.

    The artifact this gate demands is matched AS A GLOB. The caller used to truncate
    `reports/**/*.xml` to `reports` and pass it as a prefix, which exempted every file
    under reports/ — so a repository whose source lived there had that service made
    invisible and Stop exited 0 over a real regression.
    """
    if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in exempt):
        return True
    if byproducts & set(rel.split("/")) and not rel.endswith(SOURCE_SUFFIXES):
        return True
    return any(PurePosixPath(rel).match(pattern) for pattern in globs)


def material_changes(
    changed: list[str], exempt: list[str], artifact_globs: list[str] | None = None
) -> list[str]:
    """Drop paths that cannot break anything.

    The plugin's own state files land in the working tree untracked, so without this
    the gate demands a test run to justify its own bookkeeping — which trains the
    agent that the gate is noise.
    """
    byproducts = {p.rstrip("/") for p in RUN_BYPRODUCTS}
    globs = list(artifact_globs or ())
    return [rel for rel in changed if not _is_exempt(rel, exempt, globs, byproducts)]



RUN_TIMEOUT = 300

# POSIX shells report "command not found" this way, and it is the difference
# between "your tests fail" and "your test runner is not installed".
NOT_EXECUTABLE = 127

# Set on every child this gate spawns, and checked before spawning one. The VALUE is a
# per-run nonce, not a flag: a bare `export CLAUDE_BESTPRACTICE_VERIFYING=1` in a shell profile
# would otherwise switch the whole evidence gate off for every session on the machine,
# which is a recursion guard doubling as an off switch.
VERIFYING_ENV = "CLAUDE_BESTPRACTICE_VERIFYING"
NONCE_FILE = "verifying.nonce"


def _issue_nonce(ctx: GitContext) -> str:
    """Mint a token for one verification run and leave it where a child can check it.

    A per-process value cannot work — the child is a different process and would never
    match, so the guard silently stops guarding and the gate recurses. A bare flag cannot
    work either: `export CLAUDE_BESTPRACTICE_VERIFYING=1` in a shell profile would switch the
    evidence gate off everywhere. So the token is unguessable AND shared, and it only
    exists on disk while a run this gate started is actually in flight.
    """
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    store.atomic_write(store.tier_b(ctx, NONCE_FILE), nonce, mode=0o600)
    return nonce


def _retire_nonce(ctx: GitContext) -> None:
    store.tier_b(ctx, NONCE_FILE).unlink(missing_ok=True)


def _inside_our_own_run(ctx: GitContext) -> bool:
    """True only for a process this gate spawned, not for anything that set the name."""
    seen = os.environ.get(VERIFYING_ENV, "")
    if not seen:
        return False
    try:
        return seen == store.tier_b(ctx, NONCE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return False

# What running a test suite leaves behind. The gate runs the suite itself now, so
# without this the gate creates these files and then reports them to the agent as its
# own scope drift on the next Stop — turning genuinely green work into a durable
# UNVERIFIED record for a mess the gate made.
RUN_BYPRODUCTS = (
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".tox/",
    ".coverage", "htmlcov/", ".nyc_output/", "coverage/", ".gradle/",
)

# Extensions a byproduct directory is not allowed to hide. Not exhaustive by intent —
# every entry here is a language something in this repository is likely to be written in,
# and an extension missing from the list only costs an unnecessary suite run.
SOURCE_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".swift", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".m", ".mm", ".scala", ".ex", ".exs", ".sql", ".sh",
)


def run_suite(ctx: GitContext, command: list[str]) -> tuple[int, str]:
    """Run the tests and record what happened. The gate's own execution IS the evidence.

    Everything else is forgeable. A JUnit file proves only that a file exists saying the
    tests passed — writing one by hand takes four lines, and `touch` defeats any
    freshness check based on mtime. Both were demonstrated against the previous version
    of this gate. So the gate stops reading claims and runs the suite itself.
    """
    env = dict(os.environ)
    env[VERIFYING_ENV] = _issue_nonce(ctx)
    try:
        proc = subprocess.run(
            command,
            cwd=str(ctx.worktree_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_TIMEOUT,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return -1, f"test command not found: {command[0]}"
    except OSError as exc:
        return -1, f"could not run the test command: {exc}"
    except subprocess.TimeoutExpired:
        return -1, f"the suite exceeded {RUN_TIMEOUT}s and was killed"
    finally:
        _retire_nonce(ctx)

    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    if proc.returncode == NOT_EXECUTABLE:
        # The shell's "command not found". `npm test` exists and exits 127 because
        # vitest is not installed — the suite did not fail, it never ran, and calling
        # that a test failure sends the agent hunting for a bug that is not there.
        return -1, tail
    return proc.returncode, tail


def verify(ctx: GitContext, globs: list[str], changed: list[str], command: list[str] | None = None) -> Verdict:
    """Tier 1: the suite must have actually run, here, on this code, and passed.

    `changed` is passed in rather than recomputed so the caller decides what counts as
    material, and so one git invocation serves the whole gate. `command` is what turns
    a claim into evidence — without it the gate can only read an artifact, and an
    artifact on its own is unbound.
    """
    if not changed:
        return Verdict(True, "no changes to verify")

    if command:
        bound = _verify_by_running(ctx, globs, command)
        if bound is not None:
            return bound

    return _verify_by_reading(ctx, globs, changed)


def _verify_by_reading(ctx: GitContext, globs: list[str], changed: list[str]) -> Verdict:
    """Fall back to reading an artifact. Weaker, and the verdict says so."""
    candidates = find_artifacts(ctx.worktree_root, globs)
    if not candidates:
        return Verdict(
            False,
            "No machine-readable test artifact found. Run the suite so it writes one:\n"
            f"  {artifact_hint(ctx)}\n"
            "A statement that tests pass is not accepted as evidence.",
        )

    artifact = None
    for path in candidates:
        artifact = parse_artifact(path)
        if artifact:
            break
    if artifact is None:
        return Verdict(
            False,
            f"Found {candidates[0].name} but could not parse it as JUnit XML or a pytest "
            "JSON report. Emit one of those formats.",
        )

    source_mtime = newest_source_mtime(ctx.worktree_root, changed)
    if artifact.mtime < source_mtime:
        stale_by = source_mtime - artifact.mtime
        return Verdict(
            False,
            f"{artifact.path.name} is {stale_by:.0f}s older than the newest changed file. "
            "It describes code that no longer exists. Re-run the suite.",
            artifact,
        )

    if not artifact.passed:
        return Verdict(
            False,
            f"{artifact.path.name}: {artifact.detail}. Fix the failures, re-run, then finish.",
            artifact,
        )

    return Verdict(
        True,
        f"{artifact.path.name}: {artifact.detail} — UNBOUND. No test command could be run "
        "here, so this artifact was read, not witnessed, and a hand-written one is "
        "indistinguishable from a real one. Set `test_command` in "
        ".claude/claude-bestpractice/config.json to make finishing verifiable.",
        artifact,
        unverified=True,
    )


def _verify_by_running(ctx: GitContext, globs: list[str], command: list[str]) -> Verdict | None:
    """Witness the suite. Returns None to fall back to reading an artifact.

    The suite is run every time there is something material to verify. An earlier version
    cached the result against a hash of the changed files, and that cache was the single
    richest source of defects in this file: it was shared across worktrees on different
    commits, blind to gitignored state, keyed without the test command so one permissive
    run certified the tree forever, unable to clear a cached failure after the
    environment was fixed, and it hashed a path list that could exceed ARG_MAX. Every one
    of those is a way to answer "the tests pass" without the tests having passed.

    Running each time costs wall-clock. The cache cost correctness, which this gate has
    none to spare.
    """
    if _inside_our_own_run(ctx):
        # Already inside a run this gate started. The suite must never be able to
        # re-enter the gate that launched it: a project whose test command ends in a
        # Stop event would otherwise recurse until something ran out of memory, and the
        # flag was being set on every child without anything ever reading it.
        return None

    # THE RUNNER ITSELF, when one is drivable — not the command the project declares.
    # `make test` and `npm run test` are recipes the agent writes, and every round of
    # verification since round four has forged one. Cutting the wrapper out of the trust
    # path is the only move that ends that, because the count then comes from a report
    # file at a path the recipe has no name for.
    seen = witness.run(ctx)
    if seen is not None:
        return _judge_witnessed(ctx, seen)

    return _verify_by_declared_command(ctx, globs, command)


def _verify_by_declared_command(
    ctx: GitContext, globs: list[str], command: list[str]
) -> Verdict | None:
    """Fall back to the command the PROJECT declares, when no runner is drivable.

    Weaker by construction and known to be: everything this reads — the exit code, the
    output, any artifact — is written by a process whose recipe the agent controls.
    The count checks downstream are what is left when the wrapper cannot be cut out.
    """
    started = time.time()
    code, tail = run_suite(ctx, command)
    if code == -1:
        # Cannot witness. Say so and fall back rather than wedging the session:
        # an unrunnable command is a setup problem, not evidence of a bug.
        return None

    missing = _missing_runner(code, tail)
    if missing:
        # NOT recorded as a red suite: nothing about the code was observed, and filing it
        # as a failure would leave a ledger entry that no amount of fixing the code clears.
        return Verdict(
            False,
            f"Could not run the suite — {missing}.\n$ {' '.join(command)}\n{tail}\n"
            "This is an environment problem, not a code failure. Fix the runner, then the "
            "gate can judge the code.",
        )

    if code != 0:
        record_red(ctx, command, tail)
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands.\n$ {' '.join(command)}\n{tail}",
        )
    # Judge FIRST, record after. Exit 0 is not the verdict — a suite where every test
    # skipped, or one whose runner printed "1 failed" behind a swallowed status, both
    # arrive here with code 0. Writing the green record before asking those questions
    # meant the two most common fake greens each cleared the red ledger on their way to
    # being refused, so the refusal was correct and the state it left behind was a lie.
    verdict = _judge_green_run(ctx, globs, command, tail, started)
    if not (verdict.ok and not verdict.unverified):
        return verdict

    # `clear_red` returns whether it accepted this run as covering the recorded failure,
    # and that return value was DISCARDED. So a run judged too narrow to clear the red
    # record still stamped `last-green.json` — the file `claude-bp ship` reads to tell
    # the founder "Tests: green (observed by the gate)". The gate refused and reassured
    # in the same breath, which is what turned a two-step evasion into a one-step one.
    #
    # A green record now means exactly what the red record's absence means, or it is not
    # written at all.
    if clear_red(ctx, command, _executed_from_output(tail)) or red(ctx) is None:
        record_green(ctx, command)
    return verdict


def _judge_witnessed(ctx: GitContext, seen: witness.Witnessed) -> Verdict:
    """A run this gate drove itself. The only path here that reads no project-authored number."""
    command = [seen.runner]
    if seen.failed or seen.returncode != 0:
        record_red(ctx, command, seen.tail)
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands — {seen.failed} failing of "
            f"{seen.executed} run by the gate itself with {seen.runner}.\n{seen.tail}",
        )
    if seen.executed == 0:
        return Verdict(
            False,
            f"{seen.runner} ran and executed NOTHING — every test skipped, or none "
            f"collected. A run that asserts nothing is not evidence.\n{seen.tail}",
        )
    # The tree-count floor applies HERE TOO. It was written for the weak fallback path
    # and never wired into this one, so the strongest path became the one asking the
    # fewest questions: it checked that a run passed and never that the run was this
    # tree's suite. One line of `addopts = -k "not price"` therefore walked straight
    # through the path built to stop exactly that.
    declared = testcount.count_tree(ctx.worktree_root)
    if declared and not testcount.plausible(declared, seen.executed):
        return Verdict(
            True,
            f"{seen.runner} passed {seen.executed} test(s), but this tree declares "
            f"{declared}. Something narrowed the run — an `addopts` filter, a `testpaths` "
            f"entry, a marker — so this is not a witnessed pass of your suite.\n{seen.tail}",
            unverified=True,
        )

    shadow = _shadowed_package(ctx.worktree_root)
    if shadow:
        name, elsewhere = shadow
        return Verdict(
            True,
            f"{seen.runner} passed {seen.executed} test(s), but `import {name}` resolves to "
            f"{elsewhere} — outside this worktree. The suite ran against code that is not "
            "the code here, so a passing run says nothing about this tree. Usually a stale "
            "editable install: `pip install -e .` from this directory fixes it.",
            unverified=True,
        )

    if clear_red(ctx, command, seen.executed) or red(ctx) is None:
        record_green(ctx, command)
    return Verdict(True, f"{seen.executed} test(s) run by the gate itself via {seen.runner}")


def _shadowed_package(root: Path) -> tuple[str, str] | None:
    """A package that exists here but imports from somewhere else. Name and where.

    Found on a real repository: a clone of Flask with a genuine regression in `src/`
    pushed green, 491 tests passing, because a `.pth` from an unrelated editable install
    put a different copy of the same package first on `sys.path`. The gate ran the suite
    itself, observed exit 0, and was right about the exit code and wrong about the tree.

    This is the failure the clean-checkout re-run exists for, but that is gated on stage
    and every library is `prototype`, so on exactly the repositories most likely to be
    pip-installed the defence was off. This costs one interpreter start per top-level
    package, on the green path only.
    """
    candidates = []
    for parent in (root, root / "src"):
        try:
            entries = sorted(parent.iterdir())
        except OSError:
            continue
        for entry in entries:
            if (entry / "__init__.py").is_file() and not entry.name.startswith((".", "_", "test")):
                candidates.append(entry.name)

    for name in candidates[:3]:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {name},os;print(os.path.dirname({name}.__file__))"],
            capture_output=True, encoding="utf-8", errors="surrogateescape",
            cwd=str(root.parent), timeout=60,
        )
        where = proc.stdout.strip()
        if proc.returncode != 0 or not where:
            continue
        try:
            Path(where).resolve().relative_to(root.resolve())
        except ValueError:
            return name, where
    return None


def _first_artifact(root: Path, globs: list[str]) -> Artifact | None:
    for path in find_artifacts(root, globs):
        artifact = parse_artifact(path)
        if artifact:
            return artifact
    return None


def _judge_by_counts(
    ctx: GitContext, command: list[str], tail: str, executed: int | None = None
) -> Verdict:
    """Exit 0 was reported. Did anything actually run?

    Three answers, and the middle one is the one that kept being lost: a countable
    number of tests ran, zero ran, or the output said nothing countable at all.
    """
    if executed is None:
        executed = _executed_from_output(tail)
    if executed == 0:
        return Verdict(
            False,
            "The suite exited 0 but EXECUTED NOTHING — every test was skipped, or none was "
            f"collected.\n$ {' '.join(command)}\n{tail}\n"
            "A run that asserts nothing is not evidence. Make the tests runnable here, or "
            f"emit a machine-readable report: {artifact_hint(ctx)}",
        )

    # -1 is "the output said nothing I can count", and it was being treated as a pass.
    # That is the same mistake as trusting the exit code, one level down: `go test ./...`
    # prints "[no test files]" and exits 0 after the agent deletes the failing test; a
    # Makefile recipe of `true` exits 0 having run nothing; a recipe that only `printf`s
    # a junit.xml exits 0 having run nothing. All three read as -1, all three were
    # certified green, and all three DELETED the red-suite ledger on the way.
    #
    # Allowed, because plenty of legitimate runners print nothing this can parse and
    # blocking them would make the gate unusable. But `unverified` — so it cannot clear
    # a red record and cannot write a green one, which is what turned "I could not tell"
    # into "I checked and it passed" on every surface the founder reads.
    if executed < 0:
        return Verdict(
            True,
            f"suite run by the gate: exit 0, but its output reported no test counts, so "
            f"nothing here witnesses that any test ran.\n$ {' '.join(command)}\n{tail}",
            unverified=True,
        )
    # Against the tree, not against the run's own account of itself. A run that touched
    # a small fraction of the tests this repository declares is a narrowed run — the
    # recipe was scoped to one file, a filter was passed, a directory was skipped — and
    # calling that a witnessed green is how a red suite goes quiet. The threshold is
    # deliberately loose: runners expand parametrised cases, so `executed` routinely
    # exceeds `declared`, and only a large shortfall means anything.
    declared = testcount.count_tree(ctx.worktree_root)
    if declared and not testcount.plausible(declared, executed):
        # BOTH sides, because the two numbers have different authors. `declared` is read
        # off the test files by this gate; `executed` is a regex over the gated party's
        # stdout. The first cut of this check penalised only executed BELOW declared and
        # blessed overcounting as parametrisation — so the round-six forgery
        # `@echo '2 passed'` became `@echo '9999 passed'` and walked straight through.
        # One keystroke. The counter had raised the cost of the cheapest attack by
        # exactly one character.
        return Verdict(
            True,
            f"suite run by the gate: exit 0, and it reported {executed} test(s) while this "
            f"tree declares {declared}. Those do not match closely enough to be the same "
            f"suite.\n$ {' '.join(command)}",
            unverified=True,
        )
    return Verdict(True, f"suite run by the gate: exit 0, {executed} test(s) executed")


def _judge_green_run(
    ctx: GitContext, globs: list[str], command: list[str], tail: str, started: float
) -> Verdict:
    """The runner exited 0. Decide whether anything was actually asserted.

    Only an artifact this run wrote is consulted. Anything older is a different run's
    claim about different code: a year-old four-line file from another project satisfied
    the executed>0 check, and a stale FAILING artifact blocked a suite that had just
    genuinely passed. The run is the evidence; the file is at most its detail.
    """
    # The runner's own words outrank its exit code when they disagree, and disagreeing is
    # ordinary rather than exotic: a Makefile recipe prefixed with `-`, a `|| true`, a
    # wrapper that swallows the status, a CI shim that always exits 0. Every one of those
    # printed "1 failed" and handed the gate exit 0, and the gate — whose entire premise
    # is that it watches the run itself — called it green and cleared the red ledger.
    #
    # A runner does not print a failure count for a suite that passed, so this direction
    # has no false positives worth the trade: the only way to be wrong is to refuse a
    # finish over the literal text "1 failed", and refusing is the recoverable mistake.
    broke = _failures_from_output(tail)
    if broke:
        return Verdict(
            False,
            f"The runner reported {broke} FAILING and then exited 0 — something is "
            "swallowing the exit status (a `-` prefix in a Makefile recipe, a `|| true`, "
            f"a wrapper script).\n$ {' '.join(command)}\n{tail}\n"
            "Fix the tests, or stop hiding the status so a real failure can stop a push.",
        )

    artifact = _first_artifact(ctx.worktree_root, globs)
    if artifact and artifact.mtime < started:
        artifact = None
    if artifact:
        if not artifact.passed:
            return Verdict(False, f"{artifact.path.name}: {artifact.detail}.", artifact)
        artifact.bound = True
        # ...and the artifact faces the same floor as stdout does. This branch RETURNED
        # before the count check was ever reached, so a recipe of
        # `printf '<testsuite tests="1" failures="0"/>' > junit.xml` — 46 bytes — bought a
        # witnessed green against a tree declaring 41. An artifact's `tests=` attribute is
        # exactly as forgeable as an echo and has no business being trusted further.
        counted = _judge_by_counts(ctx, command, tail, executed=max(artifact.total - artifact.skipped, 0))
        if counted.unverified:
            return Verdict(True, counted.reason, artifact, unverified=True)
        return Verdict(
            True, f"suite run by the gate: exit 0; {artifact.path.name}: {artifact.detail}", artifact
        )

    # Exit zero is not "the tests passed", it is "the runner had no complaints" — and a
    # runner has no complaints about a suite in which every test was skipped. pytest
    # exits 0 on `1 skipped`, which made the skip accounting above dead code on the
    # default path: an implementation that raised NotImplementedError finished green.
    return _judge_by_counts(ctx, command, tail)


# "1 passed", "3 failed, 2 passed in 0.1s", "1 skipped in 0.01s", "no tests ran".
_OUTCOMES = re.compile(
    r"(?<![\w.])(\d+)\s+(passed|failed|errors?|xpassed|xfailed|skipped|deselected|ignored)\b"
)
_DID_NOT_RUN = {"skipped", "deselected", "ignored"}
_BROKE = {"failed", "error", "errors"}
# unittest says "FAILED (failures=1, errors=2)" rather than counting in the outcome line.
_UNITTEST_BROKE = re.compile(r"(?:failures|errors)=(\d+)")


def _failures_from_output(text: str) -> int:
    """How many tests the runner itself says broke. Zero when it says nothing."""
    counted = sum(int(n) for n, word in _OUTCOMES.findall(text) if word in _BROKE)
    return counted + sum(int(n) for n in _UNITTEST_BROKE.findall(text))
_ZERO_RAN = re.compile(r"(?i)\b(no tests ran|collected 0 items|0 tests? (?:ran|executed))\b")

# stdlib unittest reports differently from pytest — "Ran 3 tests" and "OK (skipped=3)".
# Without these, `python -m unittest` fell through to "cannot tell" and an entirely
# skipped stdlib suite was accepted, which is the same hole in a different runner. This
# project's own suite is unittest, so the default path has to understand it.
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests?\b", re.M)
_UNITTEST_SKIPPED = re.compile(r"\bskipped=(\d+)")


def _executed_from_output(text: str) -> int:
    """How many tests actually ran, read from the runner's own summary line.

    Returns -1 for "cannot tell", which the caller treats as ran. Refusing every runner
    whose output we do not recognise would block most projects on their first turn, and
    a wrong refusal is how a gate gets uninstalled. The artifact path stays strict.
    """
    if _ZERO_RAN.search(text):
        return 0

    ran = _UNITTEST_RAN.search(text)
    if ran:
        skipped = sum(int(n) for n in _UNITTEST_SKIPPED.findall(text))
        return max(int(ran.group(1)) - skipped, 0)

    outcomes = _OUTCOMES.findall(text)
    if not outcomes:
        return -1
    # `1 skipped` alone means the runner was happy and nothing was asserted.
    return sum(int(n) for n, word in outcomes if word not in _DID_NOT_RUN)


# Installed dependencies are gitignored by every ecosystem, so a clean checkout has none
# of them and the suite fails on imports rather than on the code. Symlinking the real
# ones in was the obvious fix and the wrong one: a suite that writes anything —
# node_modules/.cache, a compiled artifact, a lockfile touch — then writes through the
# link into the founder's actual dependency tree. A verification step is not allowed to
# mutate the thing it is verifying.
#
# So the missing-dependency case is detected and reported as INCONCLUSIVE instead. A
# re-run that cannot import the project proves nothing about the committed tree, and
# reporting it as a failure is worse than not running it: it is a red result no correct
# work can clear.
_MISSING_DEPENDENCY = re.compile(
    r"(?i)(ModuleNotFoundError|ImportError: No module named|Cannot find module|"
    r"cannot find package|error: could not find|command not found|"
    r"no such file or directory: '?(?:node|npm|npx|cargo|go|bundle))"
)


# The module name inside an import failure, across the runners this plugin detects.
_UNRESOLVED = re.compile(
    r"(?i)(?:ModuleNotFoundError: No module named ['\"]([\w.]+)|"
    r"ImportError: No module named ['\"]?([\w.]+)|"
    r"Cannot find module ['\"]([^'\"]+))"
)


def _uncommitted_local_modules(ctx: GitContext, tail: str) -> list[str]:
    """Of the modules the committed tree could not import, which are your own uncommitted files.

    The discriminator the exemption was missing. `pandas` missing from a bare checkout is
    a dependency problem and says nothing; `src/helper.py` missing is the regression the
    clean re-run exists to catch, and both arrive as ModuleNotFoundError.
    """
    from .gitctx import _run

    names: set[str] = set()
    for match in _UNRESOLVED.finditer(tail):
        name = next((g for g in match.groups() if g), "")
        if name:
            names.add(name.split(".")[0].strip("./"))
    if not names:
        return []

    untracked = _run(
        ["-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"],
        ctx.worktree_root, check=False,
    ).splitlines()
    out = []
    for rel in untracked:
        stem = PurePosixPath(rel).stem
        if stem in names or PurePosixPath(rel).parts[:1] and PurePosixPath(rel).parts[0] in names:
            out.append(rel)
    return sorted(set(out))[:6]


def _judge_clean_failure(ctx: GitContext, command: list[str], tail: str) -> Verdict:
    """Why the committed tree failed: your missing file, their missing package, or a bug.

    All three arrive as a non-zero exit and the first two both look like
    ModuleNotFoundError, which is why the exemption used to swallow the finding: an
    uncommitted `src/helper.py` — the literal example the README gives for why this check
    exists — disabled the check that exists for it, silently, with a reassuring message.
    """
    if not _MISSING_DEPENDENCY.search(tail):
        return Verdict(
            False,
            "The suite passes in your working tree but FAILS on the committed tree. "
            "Something you rely on is uncommitted, ignored, or local.\n"
            f"$ {' '.join(command)}\n" + tail,
        )

    yours = _uncommitted_local_modules(ctx, tail)
    if yours:
        it_is = "it is" if len(yours) == 1 else "they are"
        return Verdict(
            False,
            "The suite passes in your working tree but the committed tree cannot even "
            f"import {', '.join(yours)} — {it_is} not committed. Anyone who clones this "
            "gets a broken build.\n"
            f"  git add {' '.join(yours)}\n"
            f"$ {' '.join(command)}\n" + tail,
        )

    # A genuine missing dependency proves nothing either way, so it is recorded as
    # unverified rather than counted as a pass. A plain True here let "we could not
    # check" read as "we checked and it was fine".
    return Verdict(
        True,
        "clean re-run skipped: the committed tree has no installed dependencies, "
        "so this says nothing about the code",
        unverified=True,
    )


def clean_rerun(ctx: GitContext, command: list[str]) -> Verdict:
    """Tier 2: run the suite against the COMMITTED tree, in a throwaway worktree.

    This is what catches the whole class of green-in-my-directory results: an
    uncommitted file, a stale build artifact, a local environment variable. The
    worktree is detached and removed afterwards, so the founder's checkout is never
    touched and no branch is created.
    """
    if not command:
        return Verdict(False, "No test command configured or detected for a clean re-run.")
    if _inside_our_own_run(ctx):
        return Verdict(True, "already inside a verification run")
    if not ctx.head or ctx.head == "HEAD":
        # `git rev-parse HEAD` echoes the literal string on an unborn branch, so the
        # guard below never fired and the re-run tried to check out a commit that does
        # not exist — breaking Stop on every zero-commit repository past prototype.
        return Verdict(True, "unborn branch: nothing committed to re-run")

    tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-verify-"))
    target = tmp / "tree"
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", "--quiet", str(target), ctx.head],
            cwd=str(ctx.worktree_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if add.returncode != 0:
            return Verdict(False, f"could not create verification worktree: {add.stderr.strip()}")

        env = dict(os.environ)
        env[VERIFYING_ENV] = _issue_nonce(ctx)
        proc = subprocess.run(
            command,
            cwd=str(target),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLEAN_RERUN_TIMEOUT,
            env=env,
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
            return _judge_clean_failure(ctx, command, tail)
        return Verdict(True, "clean-checkout re-run passed")
    except subprocess.TimeoutExpired:
        return Verdict(False, f"clean re-run exceeded {CLEAN_RERUN_TIMEOUT}s and was killed")
    except OSError as exc:
        # A missing or unexecutable runner used to escape as an exception, straight past
        # the gate's escalation counter and into the fail-closed handler. That wedged the
        # session permanently: every Stop returned exit 2, the counter never advanced, and
        # the four-strikes release could never fire. A setup problem must be reportable.
        return Verdict(False, f"could not run the clean re-run ({exc}). Check the test command.")
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(target)],
            cwd=str(ctx.worktree_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(ctx.worktree_root),
            capture_output=True,
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def detect_loop(signatures: list[str], n: int = 3, repeats: int = 3) -> str | None:
    """Find an n-gram of tool signatures repeating `repeats` times consecutively.

    A 3-gram detector caught 100% of degenerate-abstention runs with zero false
    positives in the study this comes from, and it needs no model and no tests.
    """
    if len(signatures) < n * repeats:
        return None
    tail = signatures[-(n * repeats) :]
    first = tail[:n]
    for i in range(1, repeats):
        if tail[i * n : (i + 1) * n] != first:
            return None
    return " -> ".join(first)


# 127 is the shell's "command not found", and make relays it verbatim. The message text is
# checked too because a wrapper can swallow the code while still printing the reason.
# Three shapes, because three shells say it three ways and `make` relays a fourth:
#   /bin/sh: 1: pytest: not found
#   bash: pytest: command not found
#   make: definitely-not-a-real-runner: No such file or directory
#   FileNotFoundError: ... 'pytest'
# The third was found by the test for this fix, not by the report — the report only had
# the first, and a pattern built from one sample is a pattern that fits one sample.
_NOT_FOUND = re.compile(
    r"(?:^|[\s:])(?P<tool>[\w.+-]+)\s*:\s*(?:command\s+)?not found"
    r"|(?:^|[\s:])(?P<tool2>[\w.+-]+)\s*:\s*No such file or directory"
    r"|No such file or directory:\s*'?(?P<alt>[\w.+-]+)'?",
    re.I | re.M,
)


def _missing_runner(code: int, tail: str) -> str:
    """Name the tool that is absent, or "" when the suite genuinely ran.

    "The suite FAILS on the code as it stands" is a claim about the CODE, and it was
    printed verbatim when the suite never ran at all — a bare `pytest` in a Makefile that
    only resolves inside an activated virtualenv, so interactive shells had it and the
    gate's did not. Zero tests executed, zero failures, and a founder sent looking for a
    defect that was not there. Reported as issue #40.

    The two situations need opposite responses: one is "fix your environment", the other is
    "fix your code". Blocking the turn is right either way; only the diagnosis was wrong.
    """
    found = _NOT_FOUND.search(tail or "")
    tool = ""
    if found:
        tool = found.group("tool") or found.group("tool2") or found.group("alt") or ""
        # `make` names itself before naming the tool it could not run.
        if tool in ("make", "sh", "bash", "zsh"):
            tool = ""
    if tool:
        return f"`{tool}` not found on PATH (exit {code})"
    if code == 127:
        return f"the runner is not on PATH (exit {code})"
    return ""


def scope_drift(changed: list[str], task_paths: list[str], exempt: list[str]) -> list[str]:
    """Files touched that the task never mentioned.

    With the fix already applied and abstention correct, four frontier models still
    edited already-correct code on 60-90% of runs. Across parallel sessions that is
    the mechanism by which one session quietly rewrites another's work.

    A task naming a directory covers everything under it; a task naming no path at all
    disables the check rather than blocking everything, because an empty task statement
    is our failure to capture, not the agent's failure to comply.
    """
    if not task_paths:
        return []
    drift = []
    for rel in changed:
        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in exempt):
            continue
        if any(rel == p or rel.startswith(p.rstrip("/") + "/") or p in rel for p in task_paths):
            continue
        drift.append(rel)
    return drift


# ------------------------------------------------------------------ the red ledger

RED_SUITE_FILE = "failing-suite.json"


def record_red(ctx: GitContext, command: list[str], tail: str) -> None:
    """Remember that the suite is red, in COMMITTED state, until it is green again.

    Blocking the turn is not remembering. The block is spent the moment the agent
    escalates past it or the founder starts a new session, and after that a red suite is
    something nobody is tracking — which is exactly how a broken test survives for weeks
    in a repository where nobody reads the diffs.

    Tier A rather than Tier B on purpose: this has to outlive the session and be visible
    from every worktree. It travels with the branch only ONCE COMMITTED — nothing here
    commits it, and `.claude/` is untracked in a fresh repository, so on a branch nobody
    has committed the state directory on, this is a local file and no more. Saying it
    "travels with the branch" flatly was a promise the code does not keep.

    `first_seen` is preserved across re-observations so the board can say how long it has
    been broken, which is the number that makes it embarrassing enough to fix.
    """
    path = store.tier_a(ctx, RED_SUITE_FILE)
    previous = store.read_json(path, default={}) or {}
    if not isinstance(previous, dict):
        previous = {}

    # The HIGH-WATER MARK of tests seen executing on this branch, not just this run's.
    # This is what makes the record hard to clear by shrinking the suite: the agent can
    # rewrite `command`, rewrite a Makefile recipe behind an unchanged command, or delete
    # the failing test outright — but it cannot make a narrower run look like it executed
    # more tests than the wider one did.
    executed = max(_executed_from_output(tail), 0)
    declared = testcount.count_tree(ctx.worktree_root)
    store.write_json(
        path,
        {
            "command": command,
            "executed": max(executed, int(previous.get("executed") or 0)),
            # What the TREE declared when it went red, counted by this gate rather
            # than reported by the run. Deleting the failing test to go green has to
            # get past this number, and stdout cannot move it.
            "declared": max(declared, int(previous.get("declared") or 0)),
            "first_seen": previous.get("first_seen", time.time()),
            "last_seen": time.time(),
            "branch": ctx.branch,
            "tail": tail[-1_200:],
        },
        mode=0o644,
    )


GREEN_FILE = "last-green.json"


def _green_path(ctx: GitContext, branch: str = ""):
    """One file per branch, in the git common dir.

    TIER B, and both halves of that matter. The common dir is shared by every worktree of
    one clone, so a run observed in the branch's own worktree is visible to a merge decided
    from anywhere in the same clone — it used to sit in the worktree's own Tier A, which is
    why a suite that had demonstrably run came back as "no test run has ever been observed"
    (#69). And it dies with the clone, which is what keeps it evidence: a green record
    committed and pulled onto another machine would satisfy the gate there without anything
    having run, which is an assertion wearing evidence's clothes (decision 0002).

    Per branch, because the message is a claim about a branch. One file for the clone meant
    a green run on `feat/a` answered for `feat/b` — the same defect `_unverified_here` was
    fixed for, still open here in the permissive direction.
    """
    name = re.sub(r"[^A-Za-z0-9._-]", "-", branch or ctx.branch or "detached") or "detached"
    return store.tier_b(ctx, "green", f"{name}.json")


def record_green(ctx: GitContext, command: list[str]) -> None:
    """Remember that a run was OBSERVED to pass, positively.

    Needed because "no red record" and "verified green" are different states and were
    being reported as the same one. A repository where nothing has ever run has no red
    record either.
    """
    store.write_json(
        _green_path(ctx),
        {"command": command, "at": time.time(), "branch": ctx.branch},
        mode=0o644,
    )


def last_green(ctx: GitContext, branch: str = "") -> dict | None:
    """The observed green run for a branch, this session's unless one is named.

    Named explicitly when a merge is being judged: the pull request's head is the branch
    whose suite matters, not whichever branch the session's tree happens to be on (#74).

    Reads the pre-1.5 location too, and checks its branch — that record was written per
    worktree with no branch test at all, so trusting it as-is would carry the old bug
    forward for one release rather than ending it.
    """
    wanted = branch or ctx.branch
    got = store.read_json(_green_path(ctx, wanted), default=None)
    if isinstance(got, dict) and got.get("command"):
        return got
    legacy = store.read_json(store.tier_a(ctx, GREEN_FILE), default=None)
    if isinstance(legacy, dict) and legacy.get("command") and legacy.get("branch") == wanted:
        return legacy
    return None


def _covers_the_red_run(ctx: GitContext, entry: dict, executed: int | None) -> bool:
    """Whether this green run is at least as much suite as the one that went red.

    Two comparisons, and they fail differently on purpose.

    `executed` is parsed from the run's own stdout, so the gated party writes both sides
    of it — `@echo '2 passed'` satisfies it for free. It is still worth having, because it
    catches the honest-looking narrowings: a recipe scoped to one file, a filter argument,
    a directory skipped.

    The declared count is read off the test FILES by this gate. Moving it means writing
    real test declarations, which is a cost this plugin is content to impose on anyone who
    wants a red suite to go quiet. It is what stops "delete the failing test" — the single
    move a blocking Stop gate most incentivises — from being the cheapest way out.
    """
    if executed is not None and executed < int(entry.get("executed") or 0):
        return False
    was = int(entry.get("declared") or 0)
    return not (was and testcount.count_tree(ctx.worktree_root) < was)


def clear_red(
    ctx: GitContext, command: list[str] | None = None, executed: int | None = None
) -> bool:
    """A green run clears the red record only when it is the SAME run that went red.

    Returns True when a previously recorded failure was cleared.

    A different command passing says nothing about the one that failed, and the gap is
    not hypothetical — it needs no evasion to open. An agent adds a `Makefile` with a
    `test:` target scoped to the test it just wrote; `detect_test_command` prefers
    Makefiles, so the gate switches to `make test`; that narrower command passes;
    `failing-suite.json` is deleted, `last-green.json` is written, every sibling board
    drops its RED SUITE line and `claude-bp ship` reports "Tests: green (observed by the
    gate)" — while the command that actually failed still fails.

    That is worse than missing the regression. The plugin manufactures positive evidence
    for it and destroys the record that contradicted it, for a founder who reads no code.
    """
    entry = red(ctx)
    if not entry:
        return False
    if command is not None and list(entry.get("command") or []) != list(command):
        return False

    # Matching the command's NAME is not enough, and this is where the first fix fell
    # short. Three routes kept argv byte-identical while changing what it executed:
    # editing a Makefile recipe behind an unchanged `make test`; letting an honest wider
    # failure overwrite the record's `command` first and then narrowing THAT; and simply
    # deleting the failing test so the same command runs a smaller suite. The identity
    # the check compared against was written by the party being gated.
    #
    # So compare what the run DID. A green that executed fewer tests than the red run did
    # is not the same suite passing, it is a smaller suite passing, and the difference is
    # invisible in argv. Costs one comparison and catches all three routes: 2 tests -> 1,
    # 2 -> 1, and 1 -> 0.
    if not _covers_the_red_run(ctx, entry, executed):
        return False

    store.tier_a(ctx, RED_SUITE_FILE).unlink(missing_ok=True)
    return True


def red(ctx: GitContext) -> dict | None:
    got = store.read_json(store.tier_a(ctx, RED_SUITE_FILE), default=None)
    return got if isinstance(got, dict) and got.get("command") else None


def red_line(ctx: GitContext) -> str:
    """One line for the board. Silent when the suite is green."""
    entry = red(ctx)
    if not entry:
        return ""
    age = max(time.time() - float(entry.get("first_seen") or time.time()), 0)
    days, hours = int(age // 86_400), int((age % 86_400) // 3600)
    lasted = f"{days}d" if days else f"{hours}h" if hours else "just now"
    return (
        f"RED SUITE on {entry.get('branch', '?')} — failing for {lasted}. "
        f"`{' '.join(entry.get('command') or [])}` does not pass. Fix it before new work."
    )
