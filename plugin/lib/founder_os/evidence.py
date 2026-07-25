"""The evidence gate — the spine of the plugin.

The agent's prose is discarded. Completion is accepted only on a machine-readable
artifact that exists, is newer than the newest changed file, and reports passing.

Why this shape and not a smarter one:

* Self-report is measured worthless. Submit rate 0.97 against a test-verified resolve
  rate of 0.65 for the strongest model in the study; two different guard prompts moved
  it by exactly zero.
* An LLM judge does not close the gap: no configuration across five judges and five
  prompt strategies beat AUROC 0.65, while a plain TF-IDF baseline hit 0.83-0.95.
  So nothing here calls a model.
* False success collapses from ~45-76% to 3% in the one benchmark domain where the
  environment verifies state independently. Same models. The verifier is the variable.

Freshness is mtime-based *against the diff*, which is the one place mtime is the
correct clock: the question is "was this artifact produced after the code changed",
and both sides are local filesystem events.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

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

    total = failed = 0
    for suite in suites or [root]:
        total += int(suite.get("tests", 0) or 0)
        failed += int(suite.get("failures", 0) or 0) + int(suite.get("errors", 0) or 0)

    # Zero collected tests is not a pass. A suite that ran nothing is the cheapest way
    # to make a gate green, and it is indistinguishable from success in the raw counts.
    passed = total > 0 and failed == 0
    detail = f"{total - failed}/{total} passed" if total else "no tests collected"
    return Artifact(path, mtime, passed, total, failed, detail)


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
    passed = total > 0 and failed == 0 and data.get("exitcode", 0) == 0
    detail = f"{total - failed}/{total} passed"
    return Artifact(path, mtime, passed, total, failed, detail)


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


def verify(ctx: GitContext, globs: list[str], changed: list[str]) -> Verdict:
    """Tier 1: a fresh, passing, machine-readable artifact must exist.

    `changed` is passed in rather than recomputed so the caller decides what counts as
    material, and so one git invocation serves the whole gate.
    """
    if not changed:
        return Verdict(True, "no changes to verify")

    candidates = find_artifacts(ctx.worktree_root, globs)
    if not candidates:
        return Verdict(
            False,
            "No machine-readable test artifact found. Run the test suite so it writes "
            "one (for example `pytest --junitxml=junit.xml`), then finish. "
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

    return Verdict(True, f"{artifact.path.name}: {artifact.detail}", artifact)


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
