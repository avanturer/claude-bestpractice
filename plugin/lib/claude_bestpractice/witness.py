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

# Floor and margin, not a ceiling. The ceiling is DERIVED from what the harness gives the
# Stop hook, because that is the only limit that was ever real: a fixed 300 sat inside a
# 900-second hook budget, and raising it — which v1.37.0 made configurable — would only
# have moved the death of the run from our timeout to the harness's, where there is no
# message at all. The setting could not deliver what it promised, so it is gone with the
# number that made it necessary (#158).
FLOOR = 30.0
# What the gate needs after the run to parse the report, judge it and write its state.
MARGIN = 0.15


class RanOutOfTime(Exception):
    """The run was killed at the ceiling. Distinct from "no runner" and from "it failed".

    Collapsing this into None is what made the advice wrong: a killed run and an absent
    one are the same emptiness from outside, and only one of them is fixed by running the
    suite again.
    """

    def __init__(self, seconds: float) -> None:
        super().__init__(f"the run did not finish within {int(seconds)}s")
        self.seconds = seconds


def timeout_for() -> float:
    """How long the run may take: what the Stop hook is given, less what judging costs.

    Read from this plugin's own `hooks.json`, so the number can never promise more than
    the harness will wait for. A repository does not get to raise this — not because its
    suite does not deserve the time, but because the time is not ours to grant: past the
    hook's budget the harness kills the process and the founder is told nothing at all.
    When a suite genuinely does not fit, the answer is the artifact its own run wrote,
    which this gate reads.
    """
    declared = _stop_hook_budget()
    return max(FLOOR, declared * (1.0 - MARGIN))


def _stop_hook_budget() -> float:
    """Seconds the harness gives the Stop gate, from the manifest that declares it."""
    manifest = Path(__file__).resolve().parent.parent.parent / "hooks" / "hooks.json"
    try:
        import json

        declared = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FLOOR
    for entry in (declared.get("hooks") or {}).get("Stop") or []:
        for hook in entry.get("hooks") or []:
            if "evidence-gate" in str(hook.get("command") or ""):
                return float(hook.get("timeout") or FLOOR)
    return FLOOR


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
    with tempfile.TemporaryDirectory(prefix="claude-bestpractice-witness-") as scratch:
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
            timeout=timeout_for(),
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as killed:
        raise RanOutOfTime(killed.timeout or timeout_for()) from None
    except OSError:
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


def _excluded(ctx: GitContext) -> list[str]:
    """Paths this repository has told the gate to skip, as `--ignore` arguments.

    The exclusions the runner's own config declares are neutralised on purpose — one line
    of `addopts` narrowed the run to whatever still passed — and that left a repository
    with fifteen-minute snapshot tests no way to say so at all. The difference is who
    holds the pen: `config.json` is refused to the session by `pre-tool`, so this list is
    the founder's, which `pytest.ini` is not (#158).
    """
    from . import config

    try:
        wanted = list(config.load(ctx).witness_exclude)
    except (AttributeError, TypeError, ValueError):
        return []
    return [f"--ignore={name}" for name in wanted if isinstance(name, str) and name.strip()]


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
            *_excluded(ctx),
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
