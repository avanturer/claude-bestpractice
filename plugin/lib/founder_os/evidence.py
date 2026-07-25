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

Re-running is skipped only when a receipt from this gate already covers the identical
tree, and "identical" means blob hashes of the changed files rather than their mtimes.
Content-addressed both because `touch` must not buy a pass and because creating a
worktree resets every mtime in the tree, which this workflow does constantly.
"""

from __future__ import annotations

import hashlib
import json
import os
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

CLEAN_RERUN_TIMEOUT = 900


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
    for rel in changed:
        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in exempt):
            continue
        out.append(rel)
    return out


RECEIPTS_FILE = "test-receipts.json"
RUN_TIMEOUT = 900
MAX_RECEIPTS = 32

# POSIX shells report "command not found" this way, and it is the difference
# between "your tests fail" and "your test runner is not installed".
NOT_EXECUTABLE = 127


def tree_fingerprint(ctx: GitContext, changed: list[str]) -> str:
    """What "this exact code" means, content-addressed.

    Blob hashes of the working copies, not mtimes: `touch` must not change the answer,
    and a worktree checkout — which resets every mtime in the tree — must not either.
    """
    hashes = provenance.hash_paths(ctx, sorted(changed))
    material = "\n".join(f"{rel}:{hashes.get(rel, 'gone')}" for rel in sorted(changed))
    return hashlib.sha256(f"{ctx.head}\n{material}".encode()).hexdigest()[:32]


def _receipts(ctx: GitContext) -> dict:
    raw = store.read_json(store.tier_b(ctx, RECEIPTS_FILE), default={}) or {}
    return raw if isinstance(raw, dict) else {}


def record_receipt(ctx: GitContext, fingerprint: str, command: list[str], exit_code: int) -> None:
    """Remember that WE ran the suite, on exactly this code, and what it returned."""
    table = _receipts(ctx)
    table[fingerprint] = {"command": command, "exit": exit_code, "at": time.time()}
    for key in sorted(table, key=lambda k: table[k].get("at", 0))[:-MAX_RECEIPTS]:
        table.pop(key, None)
    store.write_json(store.tier_b(ctx, RECEIPTS_FILE), table)


def receipt_for(ctx: GitContext, fingerprint: str) -> dict | None:
    got = _receipts(ctx).get(fingerprint)
    return got if isinstance(got, dict) else None


def run_suite(ctx: GitContext, command: list[str], fingerprint: str) -> tuple[int, str]:
    """Run the tests and record what happened. The gate's own execution IS the evidence.

    Everything else is forgeable. A JUnit file proves only that a file exists saying the
    tests passed — writing one by hand takes four lines, and `touch` defeats any
    freshness check based on mtime. Both were demonstrated against the previous version
    of this gate. So the gate stops reading claims and runs the suite itself.
    """
    env = dict(os.environ)
    env["FOUNDER_OS_VERIFYING"] = "1"
    try:
        proc = subprocess.run(
            command,
            cwd=str(ctx.worktree_root),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            env=env,
        )
    except FileNotFoundError:
        return -1, f"test command not found: {command[0]}"
    except OSError as exc:
        return -1, f"could not run the test command: {exc}"
    except subprocess.TimeoutExpired:
        return -1, f"the suite exceeded {RUN_TIMEOUT}s and was killed"

    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    if proc.returncode == NOT_EXECUTABLE:
        # The shell's "command not found". `npm test` exists and exits 127 because
        # vitest is not installed — the suite did not fail, it never ran, and calling
        # that a test failure sends the agent hunting for a bug that is not there.
        return -1, tail
    record_receipt(ctx, fingerprint, command, proc.returncode)
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
        bound = _verify_by_running(ctx, globs, changed, command)
        if bound is not None:
            return bound

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
        f"{artifact.path.name}: {artifact.detail} (UNBOUND — no test command is "
        "configured, so this artifact was read, not witnessed)",
        artifact,
    )


def _verify_by_running(ctx: GitContext, globs: list[str], changed: list[str], command: list[str]) -> Verdict | None:
    """Witness the suite. Returns None to fall back to reading an artifact.

    Re-running is skipped when a receipt already covers this exact tree, so finishing a
    turn twice does not pay for the suite twice — but the receipt is keyed on content,
    so a single edited character invalidates it.
    """
    fingerprint = tree_fingerprint(ctx, changed)
    receipt = receipt_for(ctx, fingerprint)
    if receipt is None:
        code, tail = run_suite(ctx, command, fingerprint)
        if code == -1:
            # Cannot witness. Say so and fall back rather than wedging the session:
            # an unrunnable command is a setup problem, not evidence of a bug.
            return None
    else:
        code, tail = int(receipt.get("exit", 1)), "(from this gate's earlier run on identical code)"

    if code != 0:
        return Verdict(
            False,
            f"The suite FAILS on the code as it stands.\n$ {' '.join(command)}\n{tail}",
        )

    artifact = None
    for path in find_artifacts(ctx.worktree_root, globs):
        artifact = parse_artifact(path)
        if artifact:
            break
    if artifact and not artifact.passed:
        return Verdict(False, f"{artifact.path.name}: {artifact.detail}.", artifact)
    if artifact:
        artifact.bound = True
        return Verdict(True, f"suite run by the gate: exit 0; {artifact.path.name}: {artifact.detail}", artifact)
    return Verdict(True, f"suite run by the gate: exit 0 ({' '.join(command)})")


def clean_rerun(ctx: GitContext, command: list[str]) -> Verdict:
    """Tier 2: run the suite against the COMMITTED tree, in a throwaway worktree.

    This is what catches the whole class of green-in-my-directory results: an
    uncommitted file, a stale build artifact, a local environment variable. The
    worktree is detached and removed afterwards, so the founder's checkout is never
    touched and no branch is created.
    """
    if not command:
        return Verdict(False, "No test command configured or detected for a clean re-run.")
    if not ctx.head:
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
        env["FOUNDER_OS_VERIFYING"] = "1"
        proc = subprocess.run(
            command,
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=CLEAN_RERUN_TIMEOUT,
            env=env,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
            return Verdict(
                False,
                "The suite passes in your working tree but FAILS on the committed tree. "
                "Something you rely on is uncommitted, ignored, or local.\n"
                f"$ {' '.join(command)}\n" + "\n".join(tail),
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
