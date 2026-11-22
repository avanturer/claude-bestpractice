"""What the project's own linter says about the lines an edit just wrote (decision 0112).

The Stop gate runs the suite once a turn ends, after every edit of the turn, so a typo in
the first one is found ten edits later, by the slowest check there is, and paid for as a
turn the gate refuses. The linter the project already configured answers in a fraction of a
second about one file. Asked after each `Edit`, `Write` and `MultiEdit`, its findings on the
lines that edit wrote reach the session with the edit's result, and nothing else does.

Only a linter the project configured AND installed is run, found where the project keeps it:
ruff where a `ruff.toml`, `.ruff.toml` or a `[tool.ruff]` table governs the file, ESLint where
an `eslint.config.*` does. Nothing is fetched to run one: no `npx`, no `uv run`, which may
install, and no linter on PATH that the project never asked for. A linter is run so that it
writes nothing (`--no-fix`), never a formatter: a file rewritten under the session would no
longer hold the text its next `Edit` was written against.

Only the lines this edit wrote are reported, read off the patch Claude Code hands PostToolUse.
A file the session opens carries whatever its history left in it, and a finding on every edit
of a legacy file is a finding the session learns to read past (`discipline` holds the same
line for stubs). Advisory: the edit has already happened, and the suite still decides.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .gitctx import GitContext

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})

# How long one linter may take on one file before the edit's result goes on without it. A
# PostToolUse hook holds the session's next step, and a cold ESLint start is about a second.
SECONDS = 10
# How many findings are listed; the rest are counted, with the command that shows them all.
MOST = 12

_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Finding:
    """One thing a linter said, where it said it."""

    line: int
    column: int
    code: str
    message: str


@dataclass(frozen=True)
class Linter:
    """A linter as this module runs it: which files, which directory configures it, how it is
    started from that directory, and how its JSON is read."""

    name: str
    suffixes: tuple[str, ...]
    configures: Callable[[Path], bool]
    program: Callable[[Path, Path], list[str]]
    arguments: tuple[str, ...]
    read: Callable[[str], list[Finding]]


def _ruff_configures(directory: Path) -> bool:
    if (directory / "ruff.toml").is_file() or (directory / ".ruff.toml").is_file():
        return True
    try:
        return "[tool.ruff" in (directory / "pyproject.toml").read_text(encoding="utf-8",
                                                                         errors="replace")
    except OSError:
        return False


def _ruff_program(configured: Path, root: Path) -> list[str]:
    """The project's own ruff: a virtual environment's beside its configuration or at the top
    of the tree, else the one on PATH, which the configuration says the project uses."""
    inside = ("Scripts", "ruff.exe") if _WINDOWS else ("bin", "ruff")
    for base in dict.fromkeys((configured, root)):
        for venv in (".venv", "venv"):
            candidate = base.joinpath(venv, *inside)
            if candidate.is_file():
                return [str(candidate)]
    found = shutil.which("ruff")
    return [found] if found else []


def _ruff_read(text: str) -> list[Finding]:
    rows = json.loads(text or "[]")
    return [Finding(int(row["location"]["row"]), int(row["location"]["column"]),
                    str(row.get("code") or "syntax"), str(row.get("message") or ""))
            for row in rows if isinstance(row, dict) and isinstance(row.get("location"), dict)]


# Flat configuration only: ESLint 10 removed `.eslintrc`, and 9 needs a switch to read it.
_ESLINT_CONFIGS = tuple(f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts"))


def _eslint_configures(directory: Path) -> bool:
    return any((directory / name).is_file() for name in _ESLINT_CONFIGS)


def _eslint_program(configured: Path, root: Path) -> list[str]:
    """The project's installed ESLint, the nearest `node_modules/.bin` from its configuration
    up to the top of the tree. Never `npx`, which fetches one where none is installed."""
    name = "eslint.cmd" if _WINDOWS else "eslint"
    for directory in (configured, *configured.parents):
        candidate = directory / "node_modules" / ".bin" / name
        if candidate.is_file():
            return [str(candidate)]
        if directory == root:
            break
    return []


def _eslint_read(text: str) -> list[Finding]:
    found = []
    for result in json.loads(text or "[]"):
        for message in result.get("messages", []) if isinstance(result, dict) else []:
            if isinstance(message, dict) and message.get("line"):
                found.append(Finding(int(message["line"]), int(message.get("column") or 1),
                                     str(message.get("ruleId") or "parse"),
                                     str(message.get("message") or "")))
    return found


LINTERS = (
    Linter("ruff", (".py", ".pyi"), _ruff_configures, _ruff_program,
           ("check", "--output-format", "json", "--no-fix", "--force-exclude"), _ruff_read),
    Linter("eslint", (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"),
           _eslint_configures, _eslint_program,
           ("--format", "json", "--no-warn-ignored"), _eslint_read),
)


def edited(tool_input: dict, response: object) -> Path | None:
    """The file an edit's call wrote, as an absolute path, or None."""
    named = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not named and isinstance(response, dict):
        named = response.get("filePath")
    path = Path(str(named)) if named else None
    return path if path is not None and path.is_absolute() else None


def touched(response: object) -> set[int] | None:
    """The lines of the file as it now stands that this edit wrote, or None for all of them.

    Read off `structuredPatch`, Claude Code's hunks of the edit: a `+` line was written here,
    and a `-` line marks the line that now stands where it was. A file the call created has
    every line written. No patch at all says nothing about which lines, and so means all.
    """
    if not isinstance(response, dict) or response.get("type") == "create":
        return None
    hunks = response.get("structuredPatch")
    if not isinstance(hunks, list):
        return None
    lines: set[int] = set()
    for hunk in hunks:
        if isinstance(hunk, dict):
            lines |= _written_in(hunk)
    return lines


def _written_in(hunk: dict) -> set[int]:
    """The new file's lines one hunk wrote; its whole range where it carries no lines."""
    at = int(hunk.get("newStart") or 1)
    body = hunk.get("lines")
    if not isinstance(body, list):
        return set(range(at, at + int(hunk.get("newLines") or 0)))
    lines: set[int] = set()
    for line in body:
        mark = str(line)[:1]
        if mark in ("+", "-"):
            lines.add(at)
        if mark not in ("-", "\\"):
            at += 1
    return lines


def _governing(path: Path, linter: Linter, root: Path) -> Path | None:
    """The nearest directory from the file up to the top of the tree that configures it."""
    for directory in path.parents:
        if linter.configures(directory):
            return directory
        if directory == root:
            return None
    return None


def findings(ctx: GitContext, path: Path) -> tuple[str, list[Finding]]:
    """The first configured, installed linter for `path` and what it says about the file,
    or ("", []) where none is, or it could not be run or read."""
    root = ctx.worktree_root
    for linter in LINTERS:
        if path.suffix.lower() not in linter.suffixes:
            continue
        configured = _governing(path, linter, root)
        program = linter.program(configured, root) if configured else []
        if not program:
            continue
        try:
            ran = subprocess.run([*program, *linter.arguments, str(path)], cwd=str(configured),
                                 capture_output=True, text=True, timeout=SECONDS,
                                 stdin=subprocess.DEVNULL)
            return linter.name, linter.read(ran.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            return "", []
    return "", []


def said(ctx: GitContext, path: Path, lines: set[int] | None) -> str:
    """What the session is told about this edit, or "" where nothing on its lines was found."""
    name, found = findings(ctx, path)
    mine = sorted((item for item in found if lines is None or item.line in lines),
                  key=lambda item: (item.line, item.column))
    if not mine:
        return ""
    try:
        shown = path.relative_to(ctx.worktree_root).as_posix()
    except ValueError:
        shown = str(path)
    text = [f"claude-bestpractice: {name} finds {len(mine)} problem(s) on the lines this edit "
            f"wrote in {shown}:"]
    text += [f"  {item.line}:{item.column}  {item.code}  {item.message}" for item in mine[:MOST]]
    if len(mine) > MOST:
        text.append(f"  and {len(mine) - MOST} more.")
    return "\n".join(text)
