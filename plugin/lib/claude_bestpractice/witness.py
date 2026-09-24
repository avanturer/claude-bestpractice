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
config file is pinned to one inside the repository, GOFLAGS is overridden, and the count is
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

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import hookio, testcount
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


# pytest's own exit status for "no tests were collected".
PYTEST_NO_TESTS = 5


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

    @property
    def ran_nothing(self) -> bool:
        """Nothing executed and nothing broke — which pytest reports as exit status 5.

        Read as a failure, that status was filed as a red suite: "0 failing of 0", a record
        no fix to the code could clear, because nothing about the code had been observed.
        """
        quiet = (0, PYTEST_NO_TESTS) if self.runner == "pytest" else (0,)
        return self.executed == 0 and self.failed == 0 and self.returncode in quiet


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


# What makes a shared config file pytest's, rather than a file that merely exists:
# `pyproject.toml` configures Black and Ruff in plenty of repositories with no Python test
# in them. Empty means the file is pytest's by its name alone. In pytest's own order of
# precedence, because `_pytest_config` pins the first of them that carries its section.
_PYTEST_SECTIONS = {
    "pytest.toml": "",
    ".pytest.toml": "",
    "pytest.ini": "",
    ".pytest.ini": "",
    "pyproject.toml": "[tool.pytest",
    "tox.ini": "[pytest]",
    "setup.cfg": "[tool:pytest]",
}


def _carries_pytest(root: Path, name: str) -> bool:
    """Whether `name` here is pytest's configuration, rather than a file that exists."""
    try:
        text = (root / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _PYTEST_SECTIONS[name] in text


def _has_python_tests(root: Path) -> bool:
    """Whether pytest has anything here to run: its own configuration, or a Python test.

    A directory called `test` or `tests` used to be enough, and it is not evidence of
    Python: it is where Node's built-in runner, Cargo's integration tests and Go's end-to-end
    suites live too. Driven over one of those, pytest collected nothing and exited 5, and the
    gate reported "the suite FAILS — 0 failing of 0" over correct work, on every machine
    whose `python3` can import pytest. A Node session spent its whole turn budget on that
    refusal; in a Go repository with a `test/` directory it also stood in front of `go`.
    """
    if any(_carries_pytest(root, name) for name in _PYTEST_SECTIONS):
        return True
    return testcount.declares(root, ".py")


def run(ctx: GitContext, env: dict[str, str] | None = None,
        where: Path | None = None, seconds: float | None = None) -> Witnessed | None:
    """Drive the detected runner ourselves. None when we cannot witness anything.

    `where` is the directory to drive it in, for a repository whose suites are per
    subproject; `seconds` is what is left of the Stop hook's budget once the suites that
    already ran have spent their share of it. Both default to the whole repository and the
    whole budget, which is every single-suite repository.
    """
    root = where or ctx.worktree_root
    runner = detect(root)
    if not runner:
        return None
    # The report lands OUTSIDE the working tree. Inside it, the project's own recipe could
    # write the file before we ever run — which is the attack this exists to end.
    with tempfile.TemporaryDirectory(prefix="claude-bestpractice-witness-") as scratch:
        if runner == "pytest":
            return _run_pytest(ctx, Path(scratch), env, root, seconds)
        return _run_go(ctx, env, root, seconds)


def _budget(seconds: float | None) -> float:
    """What one witnessed run may take: the Stop hook's whole budget, or what a plan left of it.

    Floored when it is what is left, because a run handed a second is killed before it can
    report anything; the plan does not start a suite once its deadline has passed.
    """
    return timeout_for() if seconds is None else max(FLOOR, seconds)


def _spawn(ctx: GitContext, argv: list[str], env: dict[str, str] | None,
           where: Path | None, limit: float) -> subprocess.CompletedProcess | None:
    try:
        return run_bounded(argv, where or ctx.worktree_root, {**os.environ, **(env or {})}, limit)
    except OSError:
        return None


def run_bounded(argv: list[str], where: Path, env: dict[str, str],
                limit: float) -> subprocess.CompletedProcess:
    """Run a suite this gate started, for at most `limit` seconds, and leave none of it running.

    Every run the Stop gate makes goes through here, because every one of them had the same
    two holes. A timeout killed only the process the gate started, never what IT started:
    the database or dev server a suite brings up outlived the gate that was waiting on it,
    once per Stop, with nothing left to stop it. And the output came back through pipes, so
    one such process still holding the suite's stdout kept the gate waiting after the suite
    itself had exited, until the limit — a run that finished in a second reported as one
    that outran the hook.

    So the run gets a session of its own, its output goes to files nothing else can hold
    open, and when it ends — finished or killed at the limit — whatever is left of its
    process group is killed with it. Raises `RanOutOfTime` at the limit and OSError when the
    program cannot be started.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(argv, cwd=str(where), stdout=out, stderr=err, env=env,
                                start_new_session=True)
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            raise RanOutOfTime(limit) from None
        finally:
            _end(proc)
        return subprocess.CompletedProcess(argv, proc.returncode, _read(out), _read(err))


def _end(proc: subprocess.Popen) -> None:
    """Kill the process group a run leads, and reap its leader."""
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    elif proc.poll() is None:
        proc.kill()
    proc.wait()


def _read(handle) -> str:
    handle.seek(0)
    return handle.read().decode("utf-8", errors="replace")


def _tail(proc: subprocess.CompletedProcess) -> str:
    return hookio.tail_of(proc.stdout + proc.stderr)


def _pytest_config(root: Path, scratch: Path, top: Path | None = None) -> Path:
    """Which ini pytest is allowed to read: this repo's, or an empty one we write.

    Without `-c`, pytest walks UPWARD looking for a config file — so a `pytest.ini` in the
    repository's parent directory, a file the founder will never see in any diff, silently
    configured the run this gate was driving. Pinning the config to something inside the
    repository keeps every knob that shapes the run inside the thing being reviewed.

    The file pinned is the first to CARRY pytest's section, in pytest's own order. The first
    that merely existed used to win, so a `pyproject.toml` holding only `[tool.black]` was
    pinned over the `setup.cfg` or `tox.ini` that configured pytest: `pythonpath = src` was
    never read, every import failed, and a suite that passes was filed red. A file without
    the section is still pinned when none carries one — pytest reads it as empty, as it
    would itself, and the rootdir stays in the repository rather than moving to ours.

    When no file in `root` carries the section, the directories above it are searched too, up
    to `top` and never past it, as pytest searches from where it starts. A workspace member's
    `pyproject.toml` holding only `[project]` does not end pytest's search: `cd packages/api &&
    pytest` reads the workspace root's section. The gate pinned the member's file instead, so
    an `asyncio_mode = "auto"` set once for the whole workspace was lost to every suite detected
    inside it (#230).
    """
    found = _governing(root, top)
    if found is not None:
        return found
    present = [name for name in _PYTEST_SECTIONS if (root / name).is_file()]
    if present:
        return root / present[0]
    empty = scratch / "pytest.ini"
    empty.write_text("[pytest]\n", encoding="utf-8")
    return empty


def _governing(root: Path, top: Path | None) -> Path | None:
    """The nearest file carrying pytest's section, from `root` up to `top`. None when none does."""
    for here in _up_to(root, top):
        carrying = [name for name in _PYTEST_SECTIONS if _carries_pytest(here, name)]
        if carrying:
            return here / carrying[0]
    return None


def configured_apart(root: Path, top: Path) -> bool:
    """Whether the tests under `root` are configured anywhere but in `root` itself.

    By a project below it with a configuration of its own, or by a configuration above it
    that pytest would read from there. Until 1.69.2 a run over `root` read neither, so a
    count taken from such a run was a count of a different run.
    """
    found = _governing(root, top)
    return len(projects(root)) > 1 or (found is not None and found.parent != root)


def _up_to(root: Path, top: Path | None) -> list[Path]:
    """`root`, then each directory above it up to `top`. Only `root` when `top` is not above it."""
    chain = [root]
    while top is not None and top in chain[-1].parents:
        chain.append(chain[-1].parent)
    return chain


def _excluded(ctx: GitContext) -> list[str]:
    """Paths this repository has told the gate to skip, relative to the suite's directory.

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
    return [name for name in wanted if isinstance(name, str) and name.strip()]


def projects(root: Path, skip: list[str] | None = None) -> list[Path]:
    """Where pytest has to be started to run the tests under `root` as they are configured.

    `root` first, then each directory below it whose own configuration governs a test file
    there: the nearest directory above the file that carries pytest's section, which is the
    file pytest itself reads when it is handed that test. A suite with one configuration, or
    none, is `[root]` and one run, exactly as before. `skip` is `witness_exclude`, so a
    project the founder excluded is not started either.

    pytest reads ONE configuration per run, found by walking up from where the run starts
    and never down. Started at the root of a repository whose tests live in `backend/`, it
    never read `backend/pyproject.toml`, and that project's `asyncio_mode = "auto"` did not
    exist: every async fixture errored, and the gate reported 226 failing over a suite that
    `make test`, which is `cd backend && pytest`, ran green (#230). Never above `root`, for
    the reason `_pytest_config` pins a file at all.
    """
    known: dict[Path, Path] = {}
    found = {_owner(test.parent, root, known) for test in testcount.files(root, ".py", skip)}
    return [root, *sorted(found - {root})]


def _owner(directory: Path, root: Path, known: dict[Path, Path]) -> Path:
    """The nearest directory from `directory` up that carries pytest's section, or `root`.

    `known` holds every directory already answered, because the files of one project share
    all of their parents and each would otherwise read the same configuration files again.
    """
    walked: list[Path] = []
    here = directory
    while here != root and here not in known and here != here.parent:
        if any(_carries_pytest(here, name) for name in _PYTEST_SECTIONS):
            known[here] = here
            break
        walked.append(here)
        here = here.parent
    owner = known.get(here, root)
    known.update(dict.fromkeys(walked, owner))
    return owner


def _run_pytest(ctx: GitContext, scratch: Path, env: dict[str, str] | None,
                where: Path | None = None, seconds: float | None = None) -> Witnessed | None:
    """pytest, started wherever the tests under `where` are configured, counted from its reports.

    Once for a suite whose tests share one configuration, which is nearly every suite, and
    once per project where they do not (see `projects`), each run leaving the projects inside
    it to their own. The runs share one budget, and running out in any of them is running out.
    """
    root = where or ctx.worktree_root
    skipped = _excluded(ctx)
    homes = projects(root, skipped)
    budget = _budget(seconds)
    until = time.monotonic() + budget
    runs: list[tuple[Path, Witnessed]] = []
    for home in homes:
        report = scratch / f"report-{len(runs)}.xml"
        ignored = [root / name for name in skipped] + [p for p in homes if home in p.parents]
        left = until - time.monotonic() if runs else budget
        if left <= 0:
            raise RanOutOfTime(budget)
        argv = _pytest_argv(_pytest_config(home, scratch, ctx.worktree_root), report, ignored)
        try:
            proc = _spawn(ctx, argv, {**(env or {}), "PYTEST_ADDOPTS": ""}, home, left)
        except RanOutOfTime:
            raise RanOutOfTime(budget) from None
        seen = _reported(proc, report)
        if seen is None:
            return None
        runs.append((home, seen))
    return _together(ctx.worktree_root, runs)


def _pytest_argv(config: Path, report: Path, ignored: list[Path]) -> list[str]:
    """One pytest run as this gate drives it, pinned to `config`, leaving `ignored` alone.

    `-o addopts=` and an empty PYTEST_ADDOPTS neutralise the one-line attack: a single
    `addopts = -k "not price"` or `--ignore=tests/test_total.py` in a config file the gate was
    otherwise happy to honour narrowed the run to whatever still passed. The gate had taken
    the recipe out of the trust path and left the runner's CONFIGURATION in it — it chose
    where the report went, and not what was executed.

    `ignored` is absolute, because a relative `--ignore` is read from wherever pytest starts,
    and a suite configured per project starts it in more than one place.
    """
    return [
        "python3", "-m", "pytest", "-q",
        "-c", str(config),
        "-o", "addopts=",
        f"--junitxml={report}",
        *(f"--ignore={path}" for path in ignored),
    ]


def _reported(proc: subprocess.CompletedProcess | None, report: Path) -> Witnessed | None:
    """What one pytest run's own report says it did. None when there is no report to read."""
    if proc is None or not report.is_file():
        return None

    from . import evidence

    artifact = evidence.parse_artifact(report)
    if artifact is None:
        return None
    executed = max(artifact.total - artifact.skipped, 0)
    return Witnessed(proc.returncode, executed, artifact.failed, _tail(proc), "pytest")


def _together(base: Path, runs: list[tuple[Path, Witnessed]]) -> Witnessed:
    """One account of a suite that pytest had to be started in several places to run.

    The counts add up, and the status is that of the first run that broke. A run that found
    nothing to collect broke nothing: the root of a repository whose tests all live in their
    projects collects nothing by design. Each run's output is headed with where it ran,
    because pytest names every file from there, and the runs that failed come last, since the
    end of the output is the part that is kept.
    """
    if len(runs) == 1:
        return runs[0][1]
    seen = [one for _, one in runs]
    spoke = [run for run in runs if not run[1].ran_nothing]
    shown = sorted(spoke or runs, key=lambda run: not run[1].passed)
    tail = "\n".join(f"pytest in {_label(base, home)}:\n{one.tail}" for home, one in shown)
    return Witnessed(_status(seen), sum(one.executed for one in seen),
                     sum(one.failed for one in seen), hookio.tail_of(tail), "pytest")


def _status(seen: list[Witnessed]) -> int:
    """The exit status of several runs taken as one: the first that broke, if any did."""
    broke = [one.returncode for one in seen if one.returncode not in (0, PYTEST_NO_TESTS)]
    if broke:
        return broke[0]
    return 0 if any(not one.ran_nothing for one in seen) else PYTEST_NO_TESTS


def _label(base: Path, home: Path) -> str:
    """Where one run was started, the way the rest of the gate names a suite's directory."""
    if home == base:
        return "the repository"
    if base in home.parents:
        return f"{home.relative_to(base).as_posix()}/"
    return str(home)


# What the gate's own `go test` runs under in place of anything `go env -w` left behind.
# Its only job is to be non-empty; see `_run_go`. `-short=false` is what go does with no
# flags at all, and a flag every go release that reads GOFLAGS knows, so the run is the
# default one everywhere.
GOFLAGS = "-short=false"


def _run_go(ctx: GitContext, env: dict[str, str] | None,
            where: Path | None = None, seconds: float | None = None) -> Witnessed | None:
    """`go test -json` emits one event per test action on stdout.

    Go has no report-file flag, so the stream is the report — but the stream comes from
    the `go` binary this gate invoked, not from a recipe the project wrote, which is the
    property that matters.
    """
    # GOFLAGS overridden: `go env -w GOFLAGS=-run=TestAdd` is ONE command, writes
    # ~/.config/go/env outside the repository, appears in no diff and no commit, and
    # silently restricted the gate's own `go test` to a test that passes. Zero bytes
    # changed inside the thing under review.
    #
    # Overridden with a VALUE, never blanked. go reads an empty variable as an unset one
    # and falls back to that very file, so the blank this used to pass restored the attack
    # it was written to stop: a regression failed the first Stop and passed the next one,
    # after one `go env -w`, and the red record was cleared on the way. Any value at all
    # replaces the file's; this one is the default, so it replaces it with nothing.
    proc = _spawn(ctx, ["go", "test", "-json", "./..."], {**(env or {}), "GOFLAGS": GOFLAGS},
                  where, _budget(seconds))
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
