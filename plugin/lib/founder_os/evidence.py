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
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import provenance, store
from .gitctx import GitContext, changed_files

# Consecutive Stop blocks before we stop blocking and leave a durable marker instead.
# The platform overrides the hook after 8, so escalating past that just burns turns.
MAX_CONSECUTIVE_BLOCKS = 4

CLEAN_RERUN_TIMEOUT = 300


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


def material_changes(changed: list[str], exempt: list[str]) -> list[str]:
    """Drop paths that cannot break anything.

    The plugin's own state files land in the working tree untracked, so without this
    the gate demands a test run to justify its own bookkeeping — which trains the
    agent that the gate is noise.
    """
    out = []
    byproducts = {p.rstrip("/") for p in RUN_BYPRODUCTS}
    for rel in changed:
        # Prefix match for exempt PATHS; component match ONLY for the throwaway
        # directories a test run creates. Applying the component rule to every exempt
        # entry made `app/reports/generator.py`, `app/coverage/rules.py` and anything
        # under a directory called docs/ or target/ invisible to the gate — a red suite
        # in ordinary domain code finished silently green.
        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in exempt):
            continue
        if byproducts & set(rel.split("/")):
            continue
        out.append(rel)
    return out


RUN_TIMEOUT = 300

# POSIX shells report "command not found" this way, and it is the difference
# between "your tests fail" and "your test runner is not installed".
NOT_EXECUTABLE = 127

# Set on every child this gate spawns, and checked before spawning one. The VALUE is a
# per-run nonce, not a flag: a bare `export FOUNDER_OS_VERIFYING=1` in a shell profile
# would otherwise switch the whole evidence gate off for every session on the machine,
# which is a recursion guard doubling as an off switch.
VERIFYING_ENV = "FOUNDER_OS_VERIFYING"
NONCE_FILE = "verifying.nonce"


def _issue_nonce(ctx: GitContext) -> str:
    """Mint a token for one verification run and leave it where a child can check it.

    A per-process value cannot work — the child is a different process and would never
    match, so the guard silently stops guarding and the gate recurses. A bare flag cannot
    work either: `export FOUNDER_OS_VERIFYING=1` in a shell profile would switch the
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
            text=True,
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
        ".claude/founder-os/config.json to make finishing verifiable.",
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

    started = time.time()
    code, tail = run_suite(ctx, command)
    if code == -1:
        # Cannot witness. Say so and fall back rather than wedging the session:
        # an unrunnable command is a setup problem, not evidence of a bug.
        return None

    if code != 0:
        record_red(ctx, command, tail)
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands.\n$ {' '.join(command)}\n{tail}",
        )
    clear_red(ctx)
    record_green(ctx, command)

    return _judge_green_run(ctx, globs, command, tail, started)


def _first_artifact(root: Path, globs: list[str]) -> Artifact | None:
    for path in find_artifacts(root, globs):
        artifact = parse_artifact(path)
        if artifact:
            return artifact
    return None


def _judge_green_run(
    ctx: GitContext, globs: list[str], command: list[str], tail: str, started: float
) -> Verdict:
    """The runner exited 0. Decide whether anything was actually asserted.

    Only an artifact this run wrote is consulted. Anything older is a different run's
    claim about different code: a year-old four-line file from another project satisfied
    the executed>0 check, and a stale FAILING artifact blocked a suite that had just
    genuinely passed. The run is the evidence; the file is at most its detail.
    """
    artifact = _first_artifact(ctx.worktree_root, globs)
    if artifact and artifact.mtime < started:
        artifact = None
    if artifact:
        if not artifact.passed:
            return Verdict(False, f"{artifact.path.name}: {artifact.detail}.", artifact)
        artifact.bound = True
        return Verdict(
            True, f"suite run by the gate: exit 0; {artifact.path.name}: {artifact.detail}", artifact
        )

    # Exit zero is not "the tests passed", it is "the runner had no complaints" — and a
    # runner has no complaints about a suite in which every test was skipped. pytest
    # exits 0 on `1 skipped`, which made the skip accounting above dead code on the
    # default path: an implementation that raised NotImplementedError finished green.
    if _executed_from_output(tail) == 0:
        return Verdict(
            False,
            "The suite exited 0 but EXECUTED NOTHING — every test was skipped, or none was "
            f"collected.\n$ {' '.join(command)}\n{tail}\n"
            "A run that asserts nothing is not evidence. Make the tests runnable here, or "
            f"emit a machine-readable report: {artifact_hint(ctx)}",
        )
    return Verdict(True, f"suite run by the gate: exit 0 ({' '.join(command)})")


# "1 passed", "3 failed, 2 passed in 0.1s", "1 skipped in 0.01s", "no tests ran".
_OUTCOMES = re.compile(
    r"(?<![\w.])(\d+)\s+(passed|failed|errors?|xpassed|xfailed|skipped|deselected|ignored)\b"
)
_DID_NOT_RUN = {"skipped", "deselected", "ignored"}
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

    tmp = Path(tempfile.mkdtemp(prefix="founder-os-verify-"))
    target = tmp / "tree"
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", "--quiet", str(target), ctx.head],
            cwd=str(ctx.worktree_root),
            capture_output=True,
            text=True,
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
            text=True,
            timeout=CLEAN_RERUN_TIMEOUT,
            env=env,
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
            if _MISSING_DEPENDENCY.search(tail):
                return Verdict(
                    True,
                    "clean re-run skipped: the committed tree has no installed dependencies, "
                    "so this says nothing about the code",
                )
            return Verdict(
                False,
                "The suite passes in your working tree but FAILS on the committed tree. "
                "Something you rely on is uncommitted, ignored, or local.\n"
                f"$ {' '.join(command)}\n" + tail,
            )
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
            text=True,
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

    Tier A rather than Tier B on purpose: this has to outlive the session, be visible
    from every worktree, and travel with the branch. `first_seen` is preserved across
    re-observations so the board can say how long it has been broken, which is the
    number that makes it embarrassing enough to fix.
    """
    path = store.tier_a(ctx, RED_SUITE_FILE)
    previous = store.read_json(path, default={}) or {}
    store.write_json(
        path,
        {
            "command": command,
            "first_seen": previous.get("first_seen", time.time()) if isinstance(previous, dict) else time.time(),
            "last_seen": time.time(),
            "branch": ctx.branch,
            "tail": tail[-1_200:],
        },
        mode=0o644,
    )


GREEN_FILE = "last-green.json"


def record_green(ctx: GitContext, command: list[str]) -> None:
    """Remember that a run was OBSERVED to pass, positively.

    Needed because "no red record" and "verified green" are different states and were
    being reported as the same one. A repository where nothing has ever run has no red
    record either.
    """
    store.write_json(
        store.tier_a(ctx, GREEN_FILE),
        {"command": command, "at": time.time(), "branch": ctx.branch},
        mode=0o644,
    )


def last_green(ctx: GitContext) -> dict | None:
    got = store.read_json(store.tier_a(ctx, GREEN_FILE), default=None)
    return got if isinstance(got, dict) and got.get("command") else None


def clear_red(ctx: GitContext) -> bool:
    """The suite passed. Returns True when this cleared a previously recorded failure."""
    path = store.tier_a(ctx, RED_SUITE_FILE)
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
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
