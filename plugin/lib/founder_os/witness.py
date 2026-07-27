"""Run the test runner directly, and take the count from a file the recipe never sees.

Seven rounds of adversarial verification broke the evidence gate the same way each time,
because every round the gate was reading a number the gated party had written:

    round 1   an artifact file            hand-written XML
    round 4   the exit code               `-` before a Makefile recipe
    round 5   the words "N failed"        stop printing them
    round 6   the count "N passed"        `@echo '2 passed in 0.03s'`
    round 7   that count, floored         `@echo '9999 passed'` — one keystroke

Bounding the count on both sides raised the price again, to "read the tree, print a
believable number". Still two steps, still nobody's idea of proof. The pattern only ends
when the gate stops asking the project how its tests went.

So: when a real runner is detectable, this invokes THAT BINARY, with a report path of the
gate's choosing in a temp directory OUTSIDE the repository. `make test`, `npm run test`
and every other wrapper are cut out of the trust path entirely — a forged recipe is not
consulted, because the recipe is not run. The count comes back from a file the recipe has
no name for and no reason to know about.

What this still does not do, stated plainly because the README now says so too: an agent
that edits `conftest.py`, vendors a fake `pytest` onto PATH, or writes a test that asserts
nothing still gets a green. Those are visible in a diff and cost real work, which is the
whole trade — this makes forgery a thing you must commit to the repository rather than a
thing you can echo.

Falls back to None whenever it cannot be sure, and the caller treats None as "could not
witness" rather than as a pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .gitctx import GitContext

TIMEOUT = 300


@dataclass
class Witnessed:
    """A run this gate performed itself, counted from its own report."""

    returncode: int
    executed: int
    failed: int
    tail: str
    runner: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.executed > 0


def _python_has_pytest() -> bool:
    probe = subprocess.run(
        ["python3", "-c", "import pytest"], capture_output=True, timeout=60
    )
    return probe.returncode == 0


def detect(root: Path) -> str:
    """Which runner this gate can drive directly. Empty when none is available.

    Deliberately narrow. A runner belongs here only when it can be told, on the command
    line, to write a machine-readable report to an arbitrary absolute path — that is the
    property that takes the project's own build files out of the loop.
    """
    if shutil.which("python3") and _has_python_tests(root) and _python_has_pytest():
        return "pytest"
    if (root / "go.mod").exists() and shutil.which("go"):
        return "go"
    return ""


def _has_python_tests(root: Path) -> bool:
    for name in ("pytest.ini", "tox.ini", "pyproject.toml", "setup.cfg"):
        if (root / name).exists():
            return True
    return (root / "tests").is_dir() or (root / "test").is_dir()


def run(ctx: GitContext, env: dict[str, str] | None = None) -> Witnessed | None:
    """Drive the detected runner ourselves. None when we cannot witness anything."""
    runner = detect(ctx.worktree_root)
    if not runner:
        return None
    # The report lands OUTSIDE the working tree. Inside it, the project's own recipe could
    # write the file before we ever run — which is the attack this exists to end.
    with tempfile.TemporaryDirectory(prefix="founder-os-witness-") as scratch:
        if runner == "pytest":
            return _run_pytest(ctx, Path(scratch) / "report.xml", env)
        return _run_go(ctx, env)


def _spawn(ctx: GitContext, argv: list[str], env: dict[str, str] | None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            argv,
            cwd=str(ctx.worktree_root),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tail(proc: subprocess.CompletedProcess) -> str:
    return "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])


def _run_pytest(ctx: GitContext, report: Path, env: dict[str, str] | None) -> Witnessed | None:
    proc = _spawn(
        ctx, ["python3", "-m", "pytest", "-q", f"--junitxml={report}"], env
    )
    if proc is None or not report.is_file():
        return None

    from . import evidence

    artifact = evidence.parse_artifact(report)
    if artifact is None:
        return None
    executed = max(artifact.total - artifact.skipped, 0)
    return Witnessed(proc.returncode, executed, artifact.failed, _tail(proc), "pytest")


def _run_go(ctx: GitContext, env: dict[str, str] | None) -> Witnessed | None:
    """`go test -json` emits one event per test action on stdout.

    Go has no report-file flag, so the stream is the report — but the stream comes from
    the `go` binary this gate invoked, not from a recipe the project wrote, which is the
    property that matters.
    """
    proc = _spawn(ctx, ["go", "test", "-json", "./..."], env)
    if proc is None:
        return None

    executed = failed = 0
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not event.get("Test"):
            continue
        if event.get("Action") == "pass":
            executed += 1
        elif event.get("Action") == "fail":
            executed += 1
            failed += 1
    return Witnessed(proc.returncode, executed, failed, _tail(proc), "go")
