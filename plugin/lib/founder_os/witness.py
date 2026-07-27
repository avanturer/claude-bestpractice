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

Round eight then showed that taking the RECIPE out was not the same as taking the
CONFIGURATION out. One line of `addopts = --ignore=tests/test_total.py`, a `pytest.ini` in
the repository's PARENT directory, or `go env -w GOFLAGS=-run=TestAdd` — one command,
outside the repo, in no diff — all narrowed the run this gate was driving. The gate had
chosen where the report went and not what was executed. So `addopts` is blanked, the
config file is pinned to one inside the repository, GOFLAGS is cleared, and the count is
compared against the tree on this path too.

What genuinely remains, and the earlier claim here was wrong to call it all diff-visible:
a `conftest.py` that monkeypatches the bug away runs every test honestly and every test
honestly passes — nothing in that run is fake, only the process is. A test that asserts
nothing counts as a test. A vendored runner on PATH, or a `.pth` in site-packages, needs
no repository change at all. The first two live in the diff. The third does not, and no
amount of running the runner harder will surface it.

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


def _pytest_config(root: Path, scratch: Path) -> Path:
    """Which ini pytest is allowed to read: this repo's, or an empty one we write.

    Without `-c`, pytest walks UPWARD looking for a config file — so a `pytest.ini` in the
    repository's parent directory, a file the founder will never see in any diff, silently
    configured the run this gate was driving. Pinning the config to something inside the
    repository keeps every knob that shapes the run inside the thing being reviewed.
    """
    for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
        if (root / name).is_file():
            return root / name
    empty = scratch / "pytest.ini"
    empty.write_text("[pytest]\n", encoding="utf-8")
    return empty


def _run_pytest(ctx: GitContext, report: Path, env: dict[str, str] | None) -> Witnessed | None:
    # `-o addopts=` and an empty PYTEST_ADDOPTS neutralise the one-line attack: a single
    # `addopts = -k "not price"` or `--ignore=tests/test_total.py` in a config file the
    # gate was otherwise happy to honour narrowed the run to whatever still passed. The
    # gate had taken the recipe out of the trust path and left the runner's CONFIGURATION
    # in it — it chose where the report went, and not what was executed.
    proc = _spawn(
        ctx,
        [
            "python3", "-m", "pytest", "-q",
            "-c", str(_pytest_config(ctx.worktree_root, report.parent)),
            "-o", "addopts=",
            f"--junitxml={report}",
        ],
        {**(env or {}), "PYTEST_ADDOPTS": ""},
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
    # GOFLAGS blanked: `go env -w GOFLAGS=-run=TestAdd` is ONE command, writes
    # ~/.config/go/env outside the repository, appears in no diff and no commit, and
    # silently restricted the gate's own `go test` to a test that passes. Zero bytes
    # changed inside the thing under review.
    proc = _spawn(ctx, ["go", "test", "-json", "./..."], {**(env or {}), "GOFLAGS": ""})
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
