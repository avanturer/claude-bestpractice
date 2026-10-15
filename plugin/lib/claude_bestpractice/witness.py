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
from dataclasses import dataclass, replace
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

    `runner` is the runner this gate was driving when it was a run of its own (`run`), so the
    advice that follows names the same runner; empty for the project's own command.
    """

    def __init__(self, seconds: float, runner: str = "") -> None:
        super().__init__(f"the run did not finish within {int(seconds)}s")
        self.seconds = seconds
        self.runner = runner


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
    # How many of `failed` are ERRORs (setup, teardown, collection) rather than failed
    # assertions, and the first broken test with its message, from the run's own report.
    errors: int = 0
    first: str = ""
    # ERRORs this run had that did not happen again when the gate ran those tests once more,
    # with the first of them as it was. Already taken out of `failed` (#266).
    recovered: int = 0
    # ERRORs that did happen again, so the second run is no excuse for them.
    reproduced: bool = False
    # Where the report of a run that broke was kept, from the top of the tree. The gate's
    # reports were written to a temp directory deleted with the run, so the one thing that
    # could tell a database under load from a broken fixture was gone before anybody read it.
    kept: str = ""

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.executed > 0

    @property
    def errors_only(self) -> bool:
        """Every test that broke broke in setup, teardown or collection, and pytest ran the rest.

        Exit status 1, never 2: pytest stops the whole session at a collection error unless told
        otherwise, so a run interrupted there executed nothing past it, and a second run of the
        file that errored could not answer for the suite it never reached.
        """
        return (self.runner == "pytest" and self.returncode == 1
                and 0 < self.errors == self.failed)

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
        try:
            if runner != "pytest":
                return _run_go(ctx, env, root, seconds)
            seen = _run_pytest(ctx, Path(scratch), env, root, seconds)
        except RanOutOfTime as killed:
            raise RanOutOfTime(killed.seconds, runner) from None
        return _keep(ctx, root, Path(scratch), seen)


# Where the reports of the gate's own run are kept once it is over, beside the report the gate
# names for a suite (`evidence.report_path`) and out of git with it. Not `junit.xml`, which is
# the one the gate READS: a report of its own read back as the project's would be a second
# opinion from the same run.
KEPT_DIR = "gate-run"


def _keep(ctx: GitContext, root: Path, scratch: Path, seen: Witnessed | None) -> Witnessed | None:
    """`seen`, with the reports of a run that broke copied where they outlive it.

    Every run clears what the previous one kept, so a report there is always the latest run
    of that suite, and a suite that passes leaves none to be mistaken for its state.

    Scrubbed on the way, like everything else this gate keeps of a failing run: a report
    holds each failure's message and traceback, and a suite that cannot connect prints the
    DSN it was given, password and all, into a directory under the tree.
    """
    from . import evidence, redact, store, worktree

    rel = root.relative_to(ctx.worktree_root).parts if ctx.worktree_root in root.parents else ()
    home = store.tier_a(ctx, evidence.REPORTS_DIR, *rel, KEPT_DIR)
    shutil.rmtree(home, ignore_errors=True)
    reports = sorted(scratch.glob("*.xml"))
    if seen is None or seen.passed or seen.ran_nothing or not reports:
        return seen
    worktree.hide(ctx)
    try:
        for report in reports:
            text = report.read_text(encoding="utf-8", errors="replace")
            store.atomic_write(home / report.name, redact.scrub(text), mode=0o644)
    except OSError:
        return seen
    named = home / reports[0].name if len(reports) == 1 else home
    return replace(seen, kept=named.relative_to(ctx.worktree_root).as_posix())


def _budget(seconds: float | None) -> float:
    """What one witnessed run may take: the Stop hook's whole budget, or what a plan left of it.

    Floored when it is what is left, because a run handed a second is killed before it can
    report anything; the plan does not start a suite once its deadline has passed.
    """
    return timeout_for() if seconds is None else max(FLOOR, seconds)


def _spawn(ctx: GitContext, argv: list[str], env: dict[str, str] | None,
           where: Path | None, limit: float) -> subprocess.CompletedProcess | None:
    try:
        return run_bounded(argv, where or ctx.worktree_root, {**os.environ, **(env or {})}, limit,
                           cpus_for(ctx))
    except OSError:
        return None


def cpus_for(ctx: GitContext) -> int:
    """How many CPUs a suite this gate starts may use: `witness_cpus`, 0 for all of them."""
    from . import config

    try:
        return max(int(config.load(ctx).witness_cpus), 0)
    except (AttributeError, TypeError, ValueError):
        return 0


@contextlib.contextmanager
def _at_most(cpus: int):
    """Start processes inside this on at most `cpus` of the CPUs this one may use.

    A suite's runner sizes its pool by the CPUs it is allowed, not by the machine: jest takes
    `os.availableParallelism()` less one, which reads the affinity mask, and so does Go's
    GOMAXPROCS. Unbounded, one Stop on a 24-thread machine started 23 jest workers of up to
    0.9 GB each while the session itself ran nothing, and six to twelve sessions finishing on
    one WSL machine took its memory to 4 GB of 47 (#267). The mask bounds every runner at
    once, where a `--maxWorkers` would have to be known, and accepted, by each.

    Which CPUs turns on this process's id, so the gates of several sessions spread over the
    machine instead of all queueing on its first few. Where the mask cannot be read or set —
    macOS and Windows have no such call — the run is started as it always was.
    """
    getter = getattr(os, "sched_getaffinity", None)
    setter = getattr(os, "sched_setaffinity", None)
    allowed: list[int] = []
    if cpus > 0 and getter is not None and setter is not None:
        with contextlib.suppress(OSError):
            allowed = sorted(getter(0))
    pinned = False
    if len(allowed) > cpus:
        start = os.getpid() % len(allowed)
        with contextlib.suppress(OSError):
            setter(0, {allowed[(start + step) % len(allowed)] for step in range(cpus)})
            pinned = True
    try:
        yield
    finally:
        if pinned:
            with contextlib.suppress(OSError):
                setter(0, allowed)


def run_bounded(argv: list[str], where: Path, env: dict[str, str],
                limit: float, cpus: int = 0) -> subprocess.CompletedProcess:
    """Run a suite this gate started, for at most `limit` seconds, on at most `cpus` CPUs, and
    leave none of it running.

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
    program cannot be started. `cpus` is `witness_cpus`; see `_at_most`.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        with _at_most(cpus):
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


def left_out(ctx: GitContext, root: Path) -> list[str]:
    """What the gate's own run leaves out under `root`, relative to it: the founder's
    `witness_exclude`, and the test files git ignores and holds no copy of."""
    return _excluded(ctx) + ignored_tests(root)


def ignored_tests(root: Path) -> list[str]:
    """Test files under `root` that git ignores and does not track, relative to `root`.

    A file git ignores is in no commit and no clean checkout, so it is nobody's change and
    no part of the suite the trunk carries — and pytest does not read `.gitignore`. Started
    at the root, it collected `data/eval/test_providers.py` out of an ignored directory and
    the gate called a suite red whose 5,887 tests passed (#255).

    Each is named by the outermost ignored directory holding it, so a whole scratch tree
    costs one `--ignore` rather than one per file; otherwise by the file itself. Both come
    from `git check-ignore`, which names no tracked file and no directory git tracks a file
    under — so a test force-added past the rule still runs, and so does anything beside it.
    Empty when git cannot say.
    """
    from . import testcount

    found = [path.relative_to(root).as_posix() for path in testcount.files(root)]
    ignored = _git_listed(root, ["check-ignore", "-z", "--stdin"], found)
    if not ignored:
        return []
    above = sorted({directory for rel in ignored for directory in _parents_of(rel)})
    cover = set(_git_listed(root, ["check-ignore", "-z", "--stdin"], above))
    return sorted({next((d for d in _parents_of(rel) if d in cover), rel) for rel in ignored})


def _parents_of(rel: str) -> list[str]:
    """`a/b/c.py` -> [`a`, `a/b`]: outermost first."""
    parts = rel.split("/")
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


def _git_listed(root: Path, args: list[str], paths: list[str]) -> list[str]:
    """The NUL-separated paths one git command prints, given `paths` on stdin. Empty on any
    failure — `check-ignore` exits 1 for "none of them", which is an answer, not an error."""
    if not paths:
        return []
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args], input="\0".join(paths),
            cwd=str(root), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode not in (0, 1):
        return []
    return [name for name in proc.stdout.split("\0") if name]


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
    skipped = left_out(ctx, root)
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
        pinned = _pytest_config(home, scratch, ctx.worktree_root)
        cache = scratch / f"cache-{len(runs)}"
        env_of_run = {**(env or {}), "PYTEST_ADDOPTS": ""}
        try:
            proc = _spawn(ctx, _pytest_argv(pinned, report, ignored, cache), env_of_run, home, left)
        except RanOutOfTime:
            raise RanOutOfTime(budget) from None
        seen = _reported(proc, report)
        if seen is None:
            return None
        again = report.with_name(f"again-{report.name}")
        runs.append((home, _once_more(ctx, _pytest_argv(pinned, again, ignored, cache), again,
                                      env_of_run, home, until - time.monotonic(), seen)))
    return _together(ctx.worktree_root, runs)


def _once_more(ctx: GitContext, argv: list[str], report: Path, env: dict[str, str], home: Path,
               left: float, seen: Witnessed) -> Witnessed:
    """`seen`, after running the tests that errored in it a second time, and those alone, when
    every test that broke in it errored (`Witnessed.errors_only`).

    An ERROR is a test that could not be set up, torn down or collected, and on a machine
    where several sessions run their suites at once it is as often the machine as the code.
    37 tests errored in the gate's run, in three files the branch did not touch; the same
    three files run straight after it passed 41 of 41, and `make check` on the same commit
    in the same minute passed all 7419 (#266). A FAILED assertion is never run again: that is
    the code answering, and asking it twice is asking for a different answer.

    pytest picks the tests itself, from the cache of the run it just made: `--lf` runs what
    broke there, by node id, from a cache directory this gate gave both runs, outside the tree.
    Errors that did not come back are taken out of the count and named; any that did, or a
    second run that could not finish in the time left, leaves `seen` as it was, a failure.
    `argv` is the first run's, writing `report`; `--lfnf=none` makes a cache pytest cannot
    read run nothing rather than everything, which is no second run at all.
    """
    if not seen.errors_only or left <= 0:
        return seen
    try:
        proc = _spawn(ctx, [*argv, "--lf", "--lfnf=none"], env, home, left)
    except RanOutOfTime:
        return seen
    rerun = _reported(proc, report)
    if rerun is None or not rerun.passed or rerun.executed < seen.errors:
        return replace(seen, reproduced=rerun is not None and rerun.failed > 0)
    return replace(seen, returncode=0, failed=0, errors=0, recovered=seen.errors)


def _pytest_argv(config: Path, report: Path, ignored: list[Path], cache: Path) -> list[str]:
    """One pytest run as this gate drives it, pinned to `config`, leaving `ignored` alone.

    `-o addopts=` and an empty PYTEST_ADDOPTS neutralise the one-line attack: a single
    `addopts = -k "not price"` or `--ignore=tests/test_total.py` in a config file the gate was
    otherwise happy to honour narrowed the run to whatever still passed. The gate had taken
    the recipe out of the trust path and left the runner's CONFIGURATION in it — it chose
    where the report went, and not what was executed.

    `ignored` is absolute, because a relative `--ignore` is read from wherever pytest starts,
    and a suite configured per project starts it in more than one place.

    `cache` is where pytest keeps what broke, the gate's own and outside the tree, so that a
    second run of the tests that errored (`_once_more`) reads the list this run wrote and
    nothing a session could have written there first.
    """
    return [
        "python3", "-m", "pytest", "-q",
        "-c", str(config),
        "-o", "addopts=",
        "-o", f"cache_dir={cache}",
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
    return Witnessed(proc.returncode, executed, artifact.failed, _tail(proc), "pytest",
                     errors=artifact.errors, first=artifact.first)


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
                     sum(one.failed for one in seen), hookio.tail_of(tail), "pytest",
                     **_what_broke_together(seen))


def _what_broke_together(seen: list[Witnessed]) -> dict:
    """The ERRORs of several runs as one: counted together, and the first test that broke
    taken from a run still broken before one that recovered."""
    broken = [one for one in seen if not one.passed]
    return {
        "errors": sum(one.errors for one in seen),
        "first": next((one.first for one in [*broken, *seen] if one.first), ""),
        "recovered": sum(one.recovered for one in seen),
        "reproduced": any(one.reproduced for one in seen),
    }


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
