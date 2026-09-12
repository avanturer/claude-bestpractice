"""Which suites answer for which files, so a turn is judged by the ones it touched.

One test command per repository was the assumption, and it fails on the first repository
that holds two projects. A mobile-only branch — nothing under `backend/` in its diff —
was refused four times, each refusal a full backend pytest run, over a test that failed
for the contents of a local database. The jest suite that did cover the change did not
exist as far as the gate was concerned, because the gate knew one command (#206).

So a suite is a PATH and a command. The gate runs the suites the diff actually reaches,
and a suite nothing touched is neither run nor demanded.

Declared beats detected, and the two are trusted differently:

* `test_commands` in config.json is the founder's word. That file is refused to sessions
  by `pre-tool`, so a suite named there is a promise about how those files are tested, and
  a runner that cannot start is an environment problem worth refusing a finish over.
* Detection is a guess about a subdirectory. A guess that cannot run is dropped, and the
  repository-wide command answers for those files instead — exactly what happened before
  this module existed. A guess must never be able to make a finish harder than it was.

Bounded on purpose. This runs inside the Stop gate, once per turn, so the scan is shallow,
the number of runner probes is capped, and more subprojects than `MAX_SUITES` collapses
back to one wide run rather than spending the hook's whole budget on five of them.
"""

from __future__ import annotations

import itertools
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .gitctx import GitContext

# How many suites one turn may run. The Stop hook has a fixed budget and every suite in
# the plan spends part of it; past three, one run of the repository's own command is both
# cheaper and more honest than a third of a run each.
MAX_SUITES = 3

# How many directories may be probed for a runner of their own. A probe reads a marker
# file and may glob for test files, and a monorepo has no upper bound on directories.
MAX_PROBES = 8

# How many directories may be LOOKED AT, probe or not. Eight stat calls each is nothing per
# directory and unbounded across a repository with hundreds of them, and this runs in a gate.
MAX_DIRS = 200

# A subproject announces its runner with one of these. Cheap existence checks, so the
# expensive part of detection only happens where something looks like a project.
_MARKERS = (
    "package.json", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg",
    "go.mod", "Cargo.toml", "Makefile",
)

# Never a subproject: build output, dependencies, and this plugin's own state.
_SKIP = {
    "node_modules", "__pycache__", "venv", "vendor", "target", "dist", "build",
    "coverage", "htmlcov", "site-packages", "third_party", "fixtures", "testdata",
}


@dataclass(frozen=True)
class Suite:
    """A test command and the files it answers for.

    `path` is a repo-relative directory with a trailing slash, or "" for the repository
    as a whole. `declared` is whether the founder named it; see the module docstring for
    why that changes how a failure to start is treated.
    """

    path: str
    command: tuple
    declared: bool

    @property
    def label(self) -> str:
        return self.path or "the repository"

    def covers(self, relpath: str) -> bool:
        """Is this file one the suite answers for?"""
        return not self.path or relpath == self.path.rstrip("/") or relpath.startswith(self.path)

    def root(self, ctx: GitContext) -> Path:
        """The directory the command runs in."""
        return ctx.worktree_root / self.path if self.path else ctx.worktree_root


def declared(commands: Any) -> list[Suite]:
    """The suites config.json names, in path order. Malformed entries are skipped."""
    found: list[Suite] = []
    if not isinstance(commands, dict):
        return found
    for where, command in sorted(commands.items()):
        argv = shlex.split(command) if isinstance(command, str) else [
            str(part) for part in command if str(part).strip()
        ]
        path = str(where).strip().strip("/")
        if argv and path and path != ".":
            found.append(Suite(f"{path}/", tuple(argv), True))
    return found


def detected(root: Path) -> list[Suite]:
    """Subprojects that carry a runner of their own, at most two levels down.

    Two levels because that is where they are: `mobile/`, and `apps/mobile` or
    `packages/api` behind a container directory that has no runner itself. Deeper than
    that and the cost stops being worth a guess.
    """
    from . import config

    found: list[Suite] = []
    probes = 0
    for directory in itertools.islice(_candidates(root), MAX_DIRS):
        if probes >= MAX_PROBES or len(found) >= MAX_SUITES:
            break
        if not any((directory / marker).is_file() for marker in _MARKERS):
            continue
        probes += 1
        command = config.detect_test_command(directory)
        if command:
            found.append(Suite(f"{directory.relative_to(root).as_posix()}/", tuple(command), False))
    return found


def _candidates(root: Path) -> Iterator[Path]:
    """Directories that could be a subproject: depth one, then depth two."""
    for first in _children(root):
        yield first
        for second in _children(first):
            yield second


def _children(directory: Path) -> list[Path]:
    """Sub-directories worth looking at, sorted, never raising on an unreadable tree."""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    return [
        entry for entry in entries
        if entry.is_dir() and entry.name not in _SKIP and not entry.name.startswith(".")
    ]


def scoped(ctx: GitContext, cfg: Any) -> list[Suite]:
    """Every path-scoped suite this repository has, longest path first.

    Longest first is what makes `packages/api/` win over `packages/` for a file inside it:
    the first match is the most specific one, so nesting needs no special case.
    """
    known = declared(getattr(cfg, "test_commands", None))
    claimed = {suite.path for suite in known}
    for guess in detected(ctx.worktree_root):
        if guess.path not in claimed:
            known.append(guess)
    return sorted(known, key=lambda suite: len(suite.path), reverse=True)


def for_changes(ctx: GitContext, cfg: Any, changed: list[str]) -> list[Suite]:
    """The suites that must pass for these files, in the order they should run.

    The repository-wide command is appended only when something in the diff belongs to no
    scoped suite — which is every single-project repository, so nothing changes there. When
    a scoped suite covers every changed file, the wide command is not run at all, and that
    is the whole of the fix: a mobile-only diff stops being refused over a backend suite.
    """
    everything = Suite("", tuple(cfg.test_command or ()), True)
    chosen, unclaimed = _claimed([suite for suite in scoped(ctx, cfg) if suite.path], changed)
    if len(chosen) > MAX_SUITES:
        return [everything] if everything.command else chosen[:MAX_SUITES]
    if everything.command and (unclaimed or not chosen):
        chosen.append(everything)
    return chosen


def _claimed(available: list, changed: list[str]) -> tuple:
    """Which of these suites the diff reaches, and whether any file was left over.

    A file no suite claims is what puts the repository-wide command back in the plan, so
    "left over" has to be reported rather than inferred from an empty list: a diff that
    touches one scoped suite AND one unclaimed file needs both.
    """
    chosen: list = []
    unclaimed = False
    for relpath in changed:
        owner = next((suite for suite in available if suite.covers(relpath)), None)
        if owner is None:
            unclaimed = True
        elif owner not in chosen:
            chosen.append(owner)
    return chosen, unclaimed
