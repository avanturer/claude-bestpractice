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

from . import hookio, provenance, store, suites, testcount, witness
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
    # The source files the failing tests live in, when the report says. A count answers
    # "is it red"; this answers "red about what", which is what decides whether the
    # failure is the session's change or the tree it is standing in (#220).
    failures: tuple[str, ...] = ()


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
    return Artifact(path, mtime, passed, total, failed, detail, skipped,
                    failures=_junit_failures(root))


def _junit_failures(root: "ET.Element") -> tuple[str, ...]:
    """The files of the failing test cases, from the `file` attribute JUnit writers set.

    Absent in some writers, and then this is empty rather than guessed: a classname is a
    module path in Python, a suite name in Go and a describe block in Jest, and turning one
    into a filename is the kind of inference this plugin does not make about somebody's
    repository.
    """
    out: list[str] = []
    for case in root.iter("testcase"):
        if not any(case.iter("failure")) and not any(case.iter("error")):
            continue
        where = (case.get("file") or "").strip()
        if where and where not in out:
            out.append(where)
    return tuple(out[:8])


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
    return Artifact(path, mtime, passed, total, failed, _detail(executed, failed, skipped), skipped,
                    failures=_json_failures(data))


def _json_failures(data: dict) -> tuple[str, ...]:
    """The files of the failing tests in a pytest JSON report: the node id up to its `::`."""
    rows = data.get("tests")
    if not isinstance(rows, list):
        return ()
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("outcome") not in ("failed", "error"):
            continue
        where = str(row.get("nodeid") or "").split("::")[0].strip()
        if where and where not in out:
            out.append(where)
    return tuple(out[:8])


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

    A TEST DIRECTORY is exempt from scope drift and from nothing else. It is on the default
    list so that the test this gate demands is not called spill, and read here as well it
    left a turn whose whole diff was a test with nothing to verify: a new failing test, or
    an existing one broken or skipped, finished silently while the suite run by hand said
    FAILED. A change to a test is exactly a change the suite has to answer for.

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
    if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in exempt
           if not testcount.in_test_directory(p.rstrip("/") + "/")):
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


def run_suite(ctx: GitContext, command: list[str], where: Path | None = None,
              seconds: float | None = None) -> tuple[int, str]:
    """Run the tests and record what happened. The gate's own execution IS the evidence.

    Everything else is forgeable. A JUnit file proves only that a file exists saying the
    tests passed — writing one by hand takes four lines, and `touch` defeats any
    freshness check based on mtime. Both were demonstrated against the previous version
    of this gate. So the gate stops reading claims and runs the suite itself.
    """
    env = dict(os.environ)
    env[VERIFYING_ENV] = _issue_nonce(ctx)
    limit = RUN_TIMEOUT if seconds is None else max(1.0, min(RUN_TIMEOUT, seconds))
    try:
        proc = subprocess.run(
            command,
            cwd=str(where or ctx.worktree_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return -1, f"test command not found: {command[0]}"
    except OSError as exc:
        return -1, f"could not run the test command: {exc}"
    except subprocess.TimeoutExpired:
        return -1, f"the suite exceeded {int(limit)}s and was killed"
    finally:
        _retire_nonce(ctx)

    tail = hookio.tail_of(proc.stdout + proc.stderr)
    if proc.returncode == NOT_EXECUTABLE:
        # The shell's "command not found". `npm test` exists and exits 127 because
        # vitest is not installed — the suite did not fail, it never ran, and calling
        # that a test failure sends the agent hunting for a bug that is not there.
        return -1, tail
    return proc.returncode, tail


def verify(ctx: GitContext, globs: list[str], changed: list[str], command: list[str] | None = None,
           plan: list | None = None) -> Verdict:
    """Tier 1: the suite must have actually run, here, on this code, and passed.

    `changed` is passed in rather than recomputed so the caller decides what counts as
    material, and so one git invocation serves the whole gate. `command` is what turns
    a claim into evidence — without it the gate can only read an artifact, and an
    artifact on its own is unbound.

    `plan` is the suites the diff actually reaches (`suites.for_changes`). It is tried
    first and `command` is what answers for whatever it could not: a guessed subproject
    runner that will not start must leave the repository no worse verified than it was
    before anybody guessed (#206).
    """
    if not changed:
        return Verdict(True, "no changes to verify")

    wide = list(command or ())
    if plan:
        settled = _verify_the_plan(ctx, globs, changed, plan)
        if settled is not None:
            return settled
        if any(not suite.path for suite in plan):
            # Already attempted as part of the plan. Running it a second time costs the
            # hook's remaining budget to learn what it just learned.
            wide = []

    if wide:
        bound = _verify_by_running(ctx, globs, wide, changed)
        if bound is not None:
            return bound

    return _verify_by_reading(ctx, globs, changed)


def _verify_the_plan(ctx: GitContext, globs: list[str], changed: list[str], plan: list) -> Verdict | None:
    """Run the suites this change touched. None when none of them could be witnessed.

    A hard failure in any suite settles the turn immediately — there is nothing a later
    suite can say that makes a failing one pass. An unverified answer does not settle it,
    because a suite further down the plan may still be outright red, and the difference
    between "could not check" and "checked and broken" is the whole of decision 0002.

    The suites SHARE one deadline. Each one used to be handed the Stop hook's entire
    budget, which is only sound while there is exactly one of them.
    """
    deadline = time.time() + witness.timeout_for()
    answers: list = []
    for suite in plan:
        verdict = _verify_one(ctx, globs, changed, suite, deadline)
        if verdict is not None and not verdict.ok:
            return verdict
        if verdict is not None:
            answers.append((suite, verdict))
    if not answers or len(answers) != len(plan):
        return None
    return _plan_verdict(answers)


def _plan_verdict(answers: list) -> Verdict:
    """One verdict for a whole plan that passed.

    An UNVERIFIED answer from any suite carries: a suite that could only be read rather
    than witnessed does not become witnessed by standing next to one that was. A plan of
    one keeps that suite's own words, which is every single-project repository and every
    message this gate printed before plans existed.
    """
    soft = next((verdict for _, verdict in answers if verdict.unverified), None)
    if soft is not None:
        return soft
    if len(answers) == 1:
        return answers[0][1]
    return Verdict(True, "; ".join(f"{suite.label}: {v.reason[:300]}" for suite, v in answers))


def _verify_one(ctx: GitContext, globs: list[str], changed: list[str], suite,
                deadline: float | None = None) -> Verdict | None:
    """Witness ONE suite, in its own directory. None when nothing could be witnessed.

    The suite is run every time there is something material to verify. An earlier version
    cached the result against a hash of the changed files, and that cache was the single
    richest source of defects in this file: it was shared across worktrees on different
    commits, blind to gitignored state, keyed without the test command so one permissive
    run certified the tree forever, unable to clear a cached failure after the
    environment was fixed, and it hashed a path list that could exceed ARG_MAX. Every one
    of those is a way to answer "the tests pass" without the tests having passed.

    A recorded FAILURE is different in kind and is re-asserted rather than re-run, because
    the worst it can do is refuse a finish that was already refused. See `_standing_failure`.
    """
    if _inside_our_own_run(ctx):
        # Already inside a run this gate started. The suite must never be able to
        # re-enter the gate that launched it: a project whose test command ends in a
        # Stop event would otherwise recurse until something ran out of memory, and the
        # flag was being set on every child without anything ever reading it.
        return None

    tree = tree_hash(ctx)
    standing = _standing_failure(ctx, suite, tree)
    if standing is not None:
        return standing

    seconds = None if deadline is None else deadline - time.time()
    # THE RUNNER ITSELF, when one is drivable — not the command the project declares.
    # `make test` and `npm run test` are recipes the agent writes, and every round of
    # verification since round four has forged one. Cutting the wrapper out of the trust
    # path is the only move that ends that, because the count then comes from a report
    # file at a path the recipe has no name for.
    try:
        seen = witness.run(ctx, None, suite.root(ctx), seconds)
    except witness.RanOutOfTime as killed:
        # FALLS THROUGH, and this is the whole correction. A suite longer than the hook
        # lives cannot be witnessed here by anyone — the harness kills the process, and no
        # setting can grant time it does not have. Refusing outright left the repository
        # blocked on every turn; reading the artifact its own run wrote is weaker evidence,
        # says so in the verdict, and is the only thing that can be true (#158).
        return _too_long_to_witness(ctx, globs, changed, killed.seconds)
    if seen is not None:
        return _judge_witnessed(ctx, seen, suite, tree)

    return _verify_by_declared_command(ctx, globs, suite, tree, seconds)


# Paths in a failing run's output, by extension. Matched loosely and then checked against
# the filesystem, because the check is what makes it safe: a sentence that happens to read
# like a path is not one if no such file exists.
_SOURCE_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|jsx|mjs|cjs|ts|tsx|go|rb|rs|java|kt|php|cs))"
)


def failing_files(ctx: GitContext, verdict: "Verdict") -> list[str]:
    """The source files a failing verdict is about, repository-relative. Empty if unknown.

    From the report when it said — pytest's JUnit writes `file=` on every case — and
    otherwise from the text of the refusal, which is where a failing run prints them.
    Every candidate has to exist in the tree, so prose is never read as a path.
    """
    found = list(verdict.artifact.failures if verdict.artifact else ())
    found += _SOURCE_PATH.findall(verdict.reason or "")
    out: list[str] = []
    for candidate in dict.fromkeys(found):
        rel = candidate.lstrip("./")
        if rel not in out and (ctx.worktree_root / rel).is_file():
            out.append(rel)
    return out[:8]


def not_this_trees_code(ctx: GitContext, failing: list[str], changed: list[str]) -> str:
    """Why a red suite may be nothing to do with this session's change. "" when it is.

    The founder's ask: "прежде чем винить сессию, сверять — падают ли те же тесты на
    origin/main. Если на main они зелёные, это отставание дерева, и говорить надо об этом, а
    не о коде." A shared checkout three commits behind failed four tests that did not exist
    on the trunk, and the gate told a session whose work was already merged that its suite
    was red — which put it in the loop of #217 (#220).

    Answered by comparing the CODE rather than by running the trunk's suite: a second full
    run inside the longest-lived hook in this plugin costs the founder minutes on every
    refusal, and a run in a throwaway checkout of somebody else's commit has none of the
    dependencies installed. So this says what it knows — these files fail, they are not in
    your diff, and here is how they stand against the trunk — and never says the trunk is
    green, which it has not observed.

    Silent whenever any failing file IS in the session's diff. A session's own change is
    its own business, and excusing one failure in a set that includes theirs would be this
    gate helping to ship a break.
    """
    from .gitctx import trunk_ref

    trunk = trunk_ref(ctx)
    files = _none_of_them_mine(failing, changed)
    if not trunk or not files:
        return ""
    differs = _differing_from(ctx, trunk, files)
    moved = [path for path in files if path in differs]
    behind = _behind(ctx, trunk)
    if moved and behind:
        return (
            f"\n  This may not be this session's change: {', '.join(moved[:3])} fails here, is "
            f"not in this session's diff, and differs from {trunk} — and this tree is {behind} "
            f"commit(s) behind it. Bring the tree level before treating the failure as the "
            f"code's: `git merge --ff-only {trunk}` (`git rebase {trunk}` if that is refused), "
            "then run the suite again."
        )
    same = [path for path in files if path not in differs]
    if not same:
        return ""
    return (
        f"\n  This may not be this session's change either: {', '.join(same[:3])} fails on code "
        f"identical to {trunk} and is not in this session's diff, so the trunk is red here too. "
        f"Report that rather than chasing it — `git log -1 {trunk} -- {same[0]}` names what last "
        "touched it."
    )


def _none_of_them_mine(failing: list[str], changed: list[str]) -> list[str]:
    """The failing files, or nothing at all when any one of them is the session's own change.

    All or nothing on purpose: excusing one failure out of a set that includes the session's
    would be this gate helping to ship a break.
    """
    mine = {path.replace("\\", "/") for path in changed}
    files = [path for path in dict.fromkeys(failing) if path]
    return [] if any(path in mine for path in files) else files


def _differing_from(ctx: GitContext, trunk: str, files: list[str]) -> set[str]:
    """Which of these files this tree holds differently from the trunk. Empty if unaskable."""
    from .gitctx import _run

    try:
        return set(_run(["diff", "--name-only", trunk, "--", *files],
                        ctx.worktree_root, check=False).split())
    except Exception:  # noqa: BLE001 - a diagnostic line must never fail a verdict
        return set()


def _behind(ctx: GitContext, trunk: str) -> int:
    """Commits the trunk has that this tree does not. 0 when it cannot be asked."""
    from .gitctx import _run

    try:
        count = _run(["rev-list", "--count", f"HEAD..{trunk}"], ctx.worktree_root, check=False)
    except Exception:  # noqa: BLE001 - see above
        return 0
    return int(count.strip()) if count.strip().isdigit() else 0


def behind_the_trunk(ctx: GitContext) -> str:
    """How far this tree lags the trunk, as a line to add to a failure. "" when it is level.

    A red suite is a claim about the code in front of the session, and a tree that lags the
    trunk runs code the trunk has already replaced: four tests failed in a shared checkout
    three commits behind, none of them failing on the trunk, and the gate told a session whose
    work was merged that its suite was red (#217, #219, #220).

    Named rather than acted on. Running the suite again at the trunk would be a second full
    run inside the longest-lived hook in this plugin, and merging the trunk in is a decision
    about somebody's working tree.
    """
    from .gitctx import trunk_ref

    trunk = trunk_ref(ctx)
    behind = _behind(ctx, trunk) if trunk else 0
    if not behind:
        return ""
    return (
        f"\n  This tree is {behind} commit(s) behind {trunk}, so some of what just ran is code "
        "the trunk has already replaced. If these tests pass there, the tree is what is stale: "
        f"`git merge --ff-only {trunk}` and run it again before treating the failure as this "
        "session's."
    )


REASSERT_SECONDS = 3600.0


def _standing_failure(ctx: GitContext, suite, tree: str) -> Verdict | None:
    """The failure already observed for this suite on exactly this tree. None otherwise.

    Remembering a FAILURE is not the cache this module refuses to have. A cached pass
    answers "the tests pass" without the tests having passed, which is the one thing that
    must never be possible here; a cached failure can only refuse a finish that has already
    been refused, on a tree nobody has touched since it was refused for it. The asymmetry is
    the whole justification — this shortcut has no direction in which it can be generous.

    It cost a founder fourteen minutes of wall clock and four turns: the same backend suite,
    the same failure, four full runs, on a branch whose diff did not reach it and which this
    plugin had itself told to report the failure and stop (#206).

    Bounded three ways, so a suite that is red for the environment rather than the code
    cannot be held red by this: a dirty tree hashes to nothing and never matches, the
    record must name this same suite, and past `REASSERT_SECONDS` the suite runs again
    whatever the record says — a database that has since been fixed is then rediscovered
    within the hour rather than never.
    """
    entry = red(ctx)
    if not _the_same_run(entry, suite, tree):
        return None
    ran = " ".join(entry.get("command") or ["?"])
    where = "" if not suite.path else f" in {suite.path}"
    return Verdict(
        False,
        f"The suite FAILS on the code as it stands{where}.\n$ {ran}\n"
        f"{str(entry.get('tail') or '').strip()}\n"
        "This is the failure already observed on exactly this tree, so it was not run "
        "again — nothing in the tree has changed since, and the answer cannot differ. "
        "Change the code and it runs for real.",
    )


def _the_same_run(entry: dict | None, suite, tree: str) -> bool:
    """Is this record the same suite, on the same tree, seen recently enough to stand?"""
    if not entry or not tree:
        return False
    if str(entry.get("tree_hash") or "") != tree:
        return False
    if str(entry.get("path") or "") != suite.path:
        return False
    try:
        seen_at = float(entry.get("last_seen") or 0)
    except (TypeError, ValueError):
        return False
    return time.time() - seen_at <= REASSERT_SECONDS


def _too_long_to_witness(
    ctx: GitContext, globs: list[str], changed: list[str], seconds: float
) -> Verdict:
    """The suite outran the hook. Read what the project's own run left, and name why."""
    verdict = _verify_by_reading(ctx, globs, changed)
    return Verdict(
        verdict.ok,
        f"This gate's own run was stopped at {int(seconds)}s — longer than the Stop hook "
        "lives, so it cannot be witnessed here whatever the settings say. Falling back to "
        "the artifact your run wrote.\n"
        f"  {verdict.reason}\n"
        "  `witness_exclude` in .claude/claude-bestpractice/config.json names paths this "
        "gate should skip, if part of the suite is what makes it long.",
        verdict.artifact,
        unverified=True,
    )


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
        "indistinguishable from a real one. `test_command` in "
        ".claude/claude-bestpractice/config.json makes finishing verifiable — that file is "
        "refused to sessions, so this is the founder's to set, not yours.",
        artifact,
        unverified=True,
    )


def _skipped(ctx: GitContext) -> list[str]:
    """Paths the founder told the gate not to run, so the count expects their absence."""
    from . import config

    try:
        return list(config.load(ctx).witness_exclude)
    except (AttributeError, TypeError, ValueError):
        return []


def _verify_by_running(
    ctx: GitContext, globs: list[str], command: list[str], changed: list[str]
) -> Verdict | None:
    """Witness the REPOSITORY'S OWN command. Returns None to fall back to reading.

    One suite covering everything, which is what a single-project repository has and what
    answers for whatever no scoped suite claimed.
    """
    return _verify_one(ctx, globs, changed, suites.Suite("", tuple(command), True))


def _verify_by_declared_command(
    ctx: GitContext, globs: list[str], suite, tree: str = "", seconds: float | None = None
) -> Verdict | None:
    """Fall back to the command the PROJECT declares, when no runner is drivable.

    Weaker by construction and known to be: everything this reads — the exit code, the
    output, any artifact — is written by a process whose recipe the agent controls.
    The count checks downstream are what is left when the wrapper cannot be cut out.
    """
    command = list(suite.command)
    started = time.time()
    code, tail = run_suite(ctx, command, suite.root(ctx), seconds)
    if code == -1:
        # Cannot witness. Say so and fall back rather than wedging the session:
        # an unrunnable command is a setup problem, not evidence of a bug.
        return None

    missing = _missing_runner(code, tail)
    if missing:
        # A DETECTED suite is a guess, and a guess whose runner is not installed must not
        # make a finish harder than it was before anybody guessed — the repository-wide
        # command answers for those files instead. A DECLARED one is the founder's promise
        # about how these files are tested, and a broken promise is worth refusing.
        if not suite.declared:
            return None
        # NOT recorded as a red suite: nothing about the code was observed, and filing it
        # as a failure would leave a ledger entry that no amount of fixing the code clears.
        return Verdict(
            False,
            f"Could not run the suite for {suite.label} — {missing}.\n$ {' '.join(command)}\n{tail}\n"
            "This is an environment problem, not a code failure. Fix the runner, then the "
            "gate can judge the code.",
        )

    if code != 0:
        record_red(ctx, command, tail, suite, tree)
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands{'' if not suite.path else f' in {suite.path}'}."
            f"\n$ {' '.join(command)}\n{tail}" + behind_the_trunk(ctx),
        )
    # Judge FIRST, record after. Exit 0 is not the verdict — a suite where every test
    # skipped, or one whose runner printed "1 failed" behind a swallowed status, both
    # arrive here with code 0. Writing the green record before asking those questions
    # meant the two most common fake greens each cleared the red ledger on their way to
    # being refused, so the refusal was correct and the state it left behind was a lie.
    verdict = _judge_green_run(ctx, globs, command, tail, started, suite)
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
        record_green(ctx, command, suite)
    return verdict


def _judge_witnessed(ctx: GitContext, seen: witness.Witnessed, suite=None, tree: str = "") -> Verdict:
    """A run this gate drove itself. The only path here that reads no project-authored number."""
    command = [seen.runner]
    root = _root_of(ctx, suite)
    if not seen.ran_nothing and (seen.failed or seen.returncode != 0):
        record_red(ctx, command, seen.tail, suite, tree)
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands — {seen.failed} failing of "
            f"{seen.executed} run by the gate itself with {seen.runner}.\n{seen.tail}"
            + behind_the_trunk(ctx),
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
    declared = testcount.count_tree(root, _skipped(ctx))
    if declared and not testcount.plausible(declared, seen.executed):
        return Verdict(
            True,
            f"{seen.runner} passed {seen.executed} test(s), but this tree declares "
            f"{declared}. Something narrowed the run — an `addopts` filter, a `testpaths` "
            f"entry, a marker — so this is not a witnessed pass of your suite.\n{seen.tail}",
            unverified=True,
        )

    shadow = _shadowed_package(root)
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
        record_green(ctx, command, suite)
    return Verdict(True, f"{seen.executed} test(s) run by the gate itself via {seen.runner}")


def _root_of(ctx: GitContext, suite=None) -> Path:
    """The directory a suite's numbers are counted in: its own, or the whole worktree."""
    return ctx.worktree_root if suite is None else suite.root(ctx)


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
    ctx: GitContext, command: list[str], tail: str, executed: int | None = None, suite=None
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
    # Against the SUITE'S OWN subtree — a jest suite for `mobile/` measured against the
    # whole tree looks like a run that missed three thousand backend tests — and not
    # against the run's own account of itself. A run that touched a small fraction of the
    # tests declared where it ran is a narrowed run: the recipe was scoped to one file, a
    # filter was passed, a directory was skipped, and calling that a witnessed green is how
    # a red suite goes quiet. The threshold is loose because runners expand parametrised
    # cases, so `executed` routinely exceeds `declared`; only a large shortfall means anything.
    declared = testcount.count_tree(_root_of(ctx, suite), _skipped(ctx))
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
    ctx: GitContext, globs: list[str], command: list[str], tail: str, started: float, suite=None
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

    artifact = _first_artifact(_root_of(ctx, suite), globs)
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
        counted = _judge_by_counts(
            ctx, command, tail, executed=max(artifact.total - artifact.skipped, 0), suite=suite
        )
        if counted.unverified:
            return Verdict(True, counted.reason, artifact, unverified=True)
        return Verdict(
            True, f"suite run by the gate: exit 0; {artifact.path.name}: {artifact.detail}", artifact
        )

    # Exit zero is not "the tests passed", it is "the runner had no complaints" — and a
    # runner has no complaints about a suite in which every test was skipped. pytest
    # exits 0 on `1 skipped`, which made the skip accounting above dead code on the
    # default path: an implementation that raised NotImplementedError finished green.
    return _judge_by_counts(ctx, command, tail, suite=suite)


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


def clean_rerun(ctx: GitContext, command: list[str], where: str = "") -> Verdict:
    """Tier 2: run the suite against the COMMITTED tree, in a throwaway worktree.

    This is what catches the whole class of green-in-my-directory results: an
    uncommitted file, a stale build artifact, a local environment variable. The
    worktree is detached and removed afterwards, so the founder's checkout is never
    touched and no branch is created.

    `where` is the suite's own directory inside that checkout, for a repository whose
    suites are per subproject: re-running a scoped suite from the repository root would
    re-run the wide one instead, which is the thing the plan exists to avoid (#206).
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
            cwd=str(target / where if where else target),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLEAN_RERUN_TIMEOUT,
            env=env,
        )
        if proc.returncode != 0:
            tail = hookio.tail_of(proc.stdout + proc.stderr)
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


SETTLED_DRIFT = "settled-drift.json"


def settle_drift(ctx, session_id: str, paths: list[str]) -> None:
    """Remember drift the gate has already given up on, so it stops asking.

    The ceiling exists to end a turn the gate cannot resolve — but `stop_hook_active` is
    false on the first Stop of every NEW user message, so the ceiling was never consulted
    there and each message bought one guaranteed block on the same paths. A founder who
    asked an unrelated question was refused over an edit from three turns ago, forever,
    with the escape reachable only by them (#106).

    Giving up and then asking again is the gate contradicting its own decision. What it
    let go of once is recorded here, and it does not come back — the founder still sees it,
    as an unverified attempt and an open item on the board, which is where a decision the
    plugin could not make belongs.
    """
    from . import store

    path = store.tier_b(ctx, SETTLED_DRIFT)
    with store.guarded_json(path, default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        known = set(table.get(session_id) or []) | set(paths)
        table[session_id] = sorted(known)[:256]
        box[0] = table


def settled_drift(ctx, session_id: str) -> list[str]:
    from . import store

    table = store.read_json(store.tier_b(ctx, SETTLED_DRIFT), default={})
    if not isinstance(table, dict):
        return []
    return [p for p in (table.get(session_id) or []) if isinstance(p, str)]


def committed(ctx: GitContext, changed: list[str]) -> set[str]:
    """Files whose changes are already in a commit on this branch.

    Drift is about unreviewed spill, and a committed file is on its way through the flow
    this plugin enforces: it is in the branch, it will be in the pull request, and the
    review gate reads that diff. Counting it again at every Stop of an eleven-hour session
    produced a demand for 140 files to be reverted, four times in one turn, that nobody
    present could satisfy — the founder was asleep and the gate cannot be answered in prose.

    The cost is stated rather than hidden: drift now catches spill that is still
    uncommitted. Committing something to escape it is not a bypass — it is the work
    entering the flow where the next gate reads it.
    """
    dirty = _uncommitted(ctx)
    return {path for path in changed if path not in dirty}


def _uncommitted(ctx: GitContext) -> set[str]:
    import subprocess

    try:
        listed = subprocess.run(
            # Forced, not left to the repository: `status.showUntrackedFiles=no` makes a
            # bare `--porcelain` answer "clean" over a tree full of untracked files.
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=str(ctx.worktree_root),
            capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # Unreadable status is not permission to forgive everything.
        return set()
    out = set()
    for line in listed.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            out.add(path)
    return out


def landed(ctx, changed: list[str]) -> list[str]:
    """Of these, the ones whose content is already on the trunk.

    Work that has been merged is not this session's unreviewed change — it has been
    through whatever review the founder runs, and a gate that keeps calling it drift is
    describing the past. Compared by content rather than by name, because a file that
    exists on the trunk with different content is exactly the case drift is FOR.
    """
    from .gitctx import blob_sha

    settled = []
    for rel in changed:
        # The WORKING TREE's content, not HEAD's. Drift is about what the tree holds now,
        # and a session that brought a file to the trunk's content — `git checkout
        # origin/main -- <path>`, the one move that greened a suite red only because the
        # tree lagged — had that write judged against a blob it had not touched. So the
        # content already on the trunk was called drift, reverting it turned the suite red
        # again, and the two gates asked for opposite things until a person intervened
        # (#217). HEAD answers for a file that is gone from the tree.
        here = blob_sha(ctx, rel) or _blob(ctx, "HEAD", rel)
        if not here:
            continue
        for trunk in ("origin/HEAD", "origin/main", "origin/master"):
            there = _blob(ctx, trunk, rel)
            if there:
                if there == here:
                    settled.append(rel)
                break
    return settled


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _blob(ctx, rev: str, relpath: str) -> str:
    """The blob id of one path at one revision, or "" when there is not one.

    Checked against the SHA shape rather than against emptiness: `git rev-parse` ECHOES an
    argument it cannot resolve instead of failing, so an absent `origin/HEAD` came back as
    the literal string `origin/HEAD:file.py` — which is non-empty, ended the search at the
    first trunk name, and made every merged file look unmerged.
    """
    from .gitctx import _run

    out = _run(["rev-parse", f"{rev}:{relpath}"], ctx.worktree_root, check=False).strip()
    return out if _SHA.match(out) else ""


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


def record_red(ctx: GitContext, command: list[str], tail: str, suite=None, tree: str = "") -> None:
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

    `shared_with` is what stops this being broadcast as a repository-wide stop on the
    strength of a run nobody can reproduce. See `_shared_environment`.
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
    declared = testcount.count_tree(_root_of(ctx, suite), _skipped(ctx))
    store.write_json(
        path,
        {
            "command": command,
            # WHICH suite, and WHICH tree: the path keeps a scoped suite's declared count
            # comparable to itself, and the hash is what lets the next Stop re-assert this
            # failure instead of rediscovering it on a tree nobody touched (#206). Absent
            # on an older record, which reads as the whole repository on an unknown tree.
            "path": "" if suite is None else suite.path,
            "tree_hash": tree,
            "executed": max(executed, int(previous.get("executed") or 0)),
            # What the TREE declared when it went red, counted by this gate rather
            # than reported by the run. Deleting the failing test to go green has to
            # get past this number, and stdout cannot move it.
            "declared": max(declared, int(previous.get("declared") or 0)),
            "first_seen": previous.get("first_seen", time.time()),
            "last_seen": time.time(),
            "branch": ctx.branch,
            # WHERE it was seen, and whether that tree's database was its own. Both are
            # read by the board rather than by any gate: the verdict on this turn is
            # unchanged, and what they decide is whether every OTHER session in the
            # repository is told to stop.
            "tree": str(ctx.worktree_root),
            "shared_with": _shared_environment(ctx, previous),
            "tail": tail[-1_200:],
        },
        mode=0o644,
    )


def _shared_environment(ctx: GitContext, previous: dict) -> str:
    """The neighbour tree this run could have been failing for, or "" when there is none.

    A suite run in a tree that shares its database with another tree of the same clone
    fails for whatever the neighbours are doing to that database. Measured, same commit
    every time: three failures, then one on a rerun, then 3293 passed in 23s once the tree
    had a database nobody else held (#182). The record still stands — the run did fail —
    but it stops being a "fix it before new work" banner on every other session's board.

    ONCE CONFIRMED, ALWAYS CONFIRMED — on this branch. A record already written from an
    isolated run carries `shared_with: ""`, and a later noisy run must not downgrade it:
    the failure is known real, and the next run happening somewhere shared says nothing
    about that. A record from a version that predates the field has no key at all, reads
    as confirmed, and so keeps behaving exactly as it did — the conservative direction,
    because the alternative is silencing a red suite nobody has shown to be environmental.

    On THIS branch, because a confirmation is about the code that was run. There is one
    record per repository and `record_red` overwrites its `branch`, so without the test a
    failure confirmed on one branch would harden the first shared run on the next one into
    a repository-wide stop — the symptom of #182, re-entered through the side door.
    """
    from . import worktree

    if previous.get("branch") == ctx.branch and not previous.get("shared_with", ""):
        return ""
    try:
        shared = worktree.shared_database(ctx)
    except Exception:  # noqa: BLE001 - a tree that cannot be read is not a shared one
        return ""
    return f"{shared[0].name} (database `{shared[1]}`)" if shared else ""


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


def tree_hash(ctx: GitContext) -> str:
    """The content of the tracked tree, as one hash, or "" when it cannot be read.

    `HEAD^{tree}` covers what is committed; a tree carrying uncommitted work deliberately
    hashes to nothing, so nothing can ever be claimed for content that is not in the tree
    at all. Both readers need that: the same hash means the same answer, and anything else
    means run it again.

    What counts as uncommitted WORK is the question `material_changes` answers everywhere
    else in this gate, and it was answered differently here — any difference at all. In
    practice that is every live session: the gates write their own state under `.claude/`,
    and a run leaves `__pycache__` and the very artifact the gate asked for. So this
    returned "" in almost every real repository, which silently switched off both things
    that read it — the push-time skip, and the re-assertion of a failure already observed
    on this exact tree (#206).
    """
    from . import config
    from .gitctx import _run

    try:
        listed = _run(["status", "--porcelain", "--untracked-files=normal"],
                      ctx.worktree_root, check=False)
        touched = [_porcelain_path(line) for line in listed.splitlines()]
        if material_changes([p for p in touched if p], [".claude/"], config.DEFAULT_ARTIFACT_GLOBS):
            return ""
        return _run(["rev-parse", "HEAD^{tree}"], ctx.worktree_root, check=False).strip()
    except Exception:  # noqa: BLE001 - no hash means no shortcut, which is the safe answer
        return ""


# `XY <path>`, and `XY <old> -> <new>` for a rename. Matched rather than sliced at a fixed
# offset, because `gitctx._run` strips its output and so eats the leading space of an
# unstaged line — slicing then took the first character of the path with it, and every tree
# read as materially dirty over a file called `claude/...` that does not exist.
_PORCELAIN = re.compile(r"^\s*[A-Z?!]{1,2}\s+(?P<path>.+)$")


def _porcelain_path(line: str) -> str:
    """The path one `git status --porcelain` line is about, or "" when it is not one."""
    found = _PORCELAIN.match(line)
    if not found:
        return ""
    # The destination of a rename is what a commit would carry, so that is the one judged.
    return found["path"].split(" -> ")[-1].strip().strip('"')


def green_covers_tree(ctx: GitContext, branch: str = "") -> bool:
    """Was the suite observed green on exactly the tree that is here now?

    False whenever anything is uncertain — no record, no stamp, a dirty tree, a different
    tree. The only thing this can do is skip work already done; it can never accept work
    that was not.
    """
    here = tree_hash(ctx)
    if not here:
        return False
    record = last_green(ctx, branch) or {}
    if record.get("path"):
        # A scoped suite. It covered its own files and says nothing about the rest, so the
        # push runs the wide suite anyway — the direction this whole function is allowed to
        # be wrong in is "one more run than strictly needed".
        return False
    return str(record.get("tree") or "") == here


def record_green(ctx: GitContext, command: list[str], suite=None) -> bool:
    """Remember that a run was OBSERVED to pass, positively. Returns whether it also
    cleared a recorded failure.

    Needed because "no red record" and "verified green" are different states and were
    being reported as the same one. A repository where nothing has ever run has no red
    record either.

    Clearing the red record was missing here, and only here. `clear_red` was reached from
    the two paths where this plugin RUNS the suite and reads its output, never from the
    pre-push hook that reports a run the project made itself — so a branch that went red
    for two minutes stayed red to the merge gate forever, whatever passed afterwards. The
    founder saw a green suite, a gate saying "the test suite is red", and no command that
    changed either (#152).

    Same rules as everywhere else: `clear_red` still requires the SAME command, so a
    narrower suite passing cannot erase a wider one's failure. Without the run's output
    there is no executed count to check, and the declared-count guard inside `clear_red`
    stands in for it — which is why this passes `None` rather than a number it does not
    have.
    """
    cleared = clear_red(ctx, command)
    record_run(ctx, command, passed=True)
    store.write_json(
        _green_path(ctx),
        {
            "command": command,
            "at": time.time(),
            "branch": ctx.branch,
            # WHAT was green, not just when. A command, a time and a branch cannot tell
            # "green now" from "green three edits ago", so the push gate had no choice but
            # to re-run the suite it had just watched pass — five minutes per push, for an
            # answer that could not differ. The tree hash is the same content-addressed
            # evidence this plugin uses everywhere else, and it fails safe: unreadable
            # means empty means re-run.
            "tree": tree_hash(ctx),
            # WHICH suite passed. A scoped suite passing is a true fact about the files it
            # covers and NOT a licence to skip the repository's own suite before a push:
            # without this field, one green jest run in `mobile/` would have made the
            # pre-push hook skip the backend suite for the same tree (#206).
            "path": "" if suite is None else suite.path,
        },
        mode=0o644,
    )
    return cleared


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


RUN_FILE = "runs"


def _run_path(ctx: GitContext, branch: str = ""):
    """Where "a suite ran here" is written, beside "and it passed". Tier B, per branch."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", branch or ctx.branch or "detached") or "detached"
    return store.tier_b(ctx, RUN_FILE, f"{name}.json")


def _lasted(stamp: object) -> str:
    """How long ago, as the boards say it: "2d", "3h", or "just now".

    Shared by the red ledger and the run record because they are the same sentence about
    two records. `_ago` cannot serve here — its one-hour floor exists so a fresh failure
    does not read "0h ago", and a run that happened forty seconds ago would inherit that
    floor and claim an hour.
    """
    try:
        age = max(time.time() - float(stamp or time.time()), 0)
    except (TypeError, ValueError):
        return "just now"
    days, hours = int(age // 86_400), int((age % 86_400) // 3600)
    return f"{days}d" if days else f"{hours}h" if hours else "just now"


def record_run(ctx: GitContext, command: list[str], passed: bool) -> None:
    """Remember that a suite RAN, whatever it then said.

    Separate from the green record because they answer different questions, and the
    difference was being reported as one. `last_green` is absent both when nothing ever
    ran and when something ran and failed, so three surfaces said "no test run has ever
    been observed on this branch" about a branch whose full suite had just run for
    fourteen minutes inside this plugin's own pre-push hook — 3436 passed, 1 failed. The
    hook saw the exit code and threw the observation away, and the Stop gate then re-ran
    the same suite to rediscover it (#198).

    NOT the red ledger, and this is the whole reason it is its own record. That ledger
    blocks a merge until THE SAME COMMAND passes, and the hook's command is routinely not
    the gate's: the hook runs the project's `make test`, while the gate drives `pytest`
    directly when it can, so a failure banked there could be cleared by neither — and the
    founder's own workflow, a `--no-verify` push, removes the one later hook run that
    could have. A blocker nothing can clear is worse than the wrong sentence it replaced.
    So this records the fact and nothing more; what blocks is still what the gate ran.
    """
    store.write_json(
        _run_path(ctx),
        {
            "command": list(command),
            "passed": bool(passed),
            "at": time.time(),
            "branch": ctx.branch,
        },
    )


def last_run(ctx: GitContext, branch: str = "") -> dict | None:
    """The last run observed on a branch, or None when none ever was.

    Falls back to the green record, which is a run by definition. Without that, every
    branch already carrying a green from before this record existed would have reported
    "never observed" the moment this shipped — the repair for this change, and the reason
    it needs no step in `migrate._REPAIRS`: the older record still answers the question.
    """
    got = store.read_json(_run_path(ctx, branch), default=None)
    if isinstance(got, dict) and got.get("command"):
        return got
    green = last_green(ctx, branch)
    return {**green, "passed": True} if green else None


def unproven(ctx: GitContext, branch: str = "") -> str:
    """Why this branch has no passing run, in terms of what was actually seen. "" when green.

    One sentence for the three surfaces that used to compose their own, because they were
    composing the same false one: absence of a green was reported as absence of a run.
    """
    if last_green(ctx, branch):
        return ""
    seen = last_run(ctx, branch)
    where = f" on {branch}" if branch else " on this branch"
    if not seen:
        return f"no test run has ever been observed{where}"
    ran = " ".join(seen.get("command") or ["?"])
    return (f"the last run observed{where} FAILED ({_lasted(seen.get('at'))}) — `{ran}`; "
            "nothing has passed since")


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
    # In the subtree the record was written for, or the comparison is between two different
    # things: a scoped suite's count against the whole repository's is always a shortfall,
    # and a red record for `mobile/` could then never be cleared by anything (#206).
    here = ctx.worktree_root / str(entry.get("path") or "")
    return not (was and testcount.count_tree(here) < was)


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


def red_problem(ctx: GitContext, branch: str = "") -> str:
    """The blocker line for a recorded failure, naming the run it judged. "" when green.

    "the test suite is red" over a suite that passes is unanswerable from outside: a real
    failure and a record left behind by a two-minute one ten days ago read identically,
    and the only ways forward are to guess at the invocation or to ignore the gate — the
    two things a merge gate must not push somebody toward (#152).

    One builder for both callers, because the merge gate asks about a pull request's head
    and the ship report asks about this branch, and a message that differed between them
    would be two answers to the same question.
    """
    entry = red(ctx)
    if not entry or (branch and entry.get("branch") != branch):
        return ""
    ran = " ".join(entry.get("command") or ["?"])
    # Named, never dropped. A red run in a shared tree is still a failure somebody has to
    # resolve, and a merge gate that waved it through on "it might have been the
    # neighbours" would be accepting an assertion in place of a run (decision 0002). What
    # changes is that the reader is told where to re-run it to find out.
    shared = str(entry.get("shared_with") or "")
    where = f", in a tree sharing its database with {shared}" if shared else ""
    # WHICH suite, when the repository has more than one. "the test suite is red" over a
    # repository with a backend and a mobile app names neither, and the reader then cannot
    # tell whether it is about the code in front of them.
    scoped = f" for {entry['path']}" if entry.get("path") else ""
    return (f"the test suite is red{scoped} — recorded for `{ran}`{_ago(entry)}{where}; "
            "that same command passing clears it")


def _ago(entry: dict) -> str:
    """How long the failure has stood, or nothing when the record cannot say.

    `first_seen`, which is the field this record actually carries and is preserved across
    re-observations — so this is how long the branch has been broken, not when it was last
    noticed. An absent stamp reads as absent: the first version defaulted it to zero and
    announced "20685d ago", which is 1970 wearing the clothes of a measurement.
    """
    try:
        stamped = float(entry.get("first_seen") or 0)
    except (TypeError, ValueError):
        return ""
    since = time.time() - stamped
    if not stamped or since <= 0:
        return ""
    days = int(since // 86400)
    return f", {days}d ago" if days else f", {max(1, int(since // 3600))}h ago"


def red(ctx: GitContext) -> dict | None:
    got = store.read_json(store.tier_a(ctx, RED_SUITE_FILE), default=None)
    return got if isinstance(got, dict) and got.get("command") else None


def red_line(ctx: GitContext) -> str:
    """One line for the board. Silent when the suite is green."""
    entry = red(ctx)
    if not entry:
        return ""
    lasted = _lasted(entry.get("first_seen"))
    ran = " ".join(entry.get("command") or [])
    shared = str(entry.get("shared_with") or "")
    if shared:
        # NOT "fix it before new work". This line reaches every session in the repository
        # at once, and on a run that shared its database with a neighbour it idled ten
        # chats over a suite that passed on the same commit in an isolated tree, and sent
        # one of them hunting a schema mismatch that did not exist (#182). It still says
        # what happened and who saw it; what it no longer does is stop everybody else on
        # a result nobody has reproduced.
        seen_in = Path(str(entry.get("tree") or "")).name or "another tree"
        return (
            f"SUITE RED in {seen_in} — `{ran}` failed there {lasted} ago, but that tree "
            f"shares its database with {shared}, so the failure may be the neighbours "
            "rather than the code. A run where the database is nobody else's decides it."
        )
    return (
        f"RED SUITE on {entry.get('branch', '?')} — failing for {lasted}. "
        f"`{ran}` does not pass. Fix it before new work."
    )
