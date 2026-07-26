"""Where the checks run: locally before every push, or on a hosted runner.

Local is the default, and it is a considered default rather than a cheap one.

Hosted minutes are metered, and this product's operating mode is three to eight sessions
pushing all day — the same commit volume as a small team, billed to one account. Worse,
the feedback arrives minutes after the mistake and lands in an inbox, so the loop that
catches a broken test is asynchronous to the loop that wrote it. A pre-push hook runs the
same gates, for free, in time to stop the push.

Hosted CI is still worth enabling for a repository other people pull from, because a
pre-push hook binds only the machines that installed it, and a plugin that can be skipped
by not installing it is advisory. Both can run at once; they are not alternatives.

The hook is installed by default at setup. That is deliberate: an opt-in check that
nobody opted into is the same as no check, and this whole project exists because the
things that must hold cannot be left to anyone remembering them.
"""

from __future__ import annotations

import contextlib
import shutil
import stat
import subprocess
from pathlib import Path

from .gitctx import GitContext

HOOK_NAME = "pre-push"
MARKER = "# founder-os pre-push gate"
BACKUP_SUFFIX = ".founder-os.bak"

# A hook we displaced is moved here and CHAINED, never merely copied aside. Copying it
# aside is what "backup" sounds like and is not what the founder needs: their husky or
# lefthook pre-push stopped running the moment ours landed, silently, and a check that
# stopped running is the exact failure this project exists to prevent — committed by the
# thing that prevents it.
DISPLACED_NAME = "pre-push.founder-os-original"
CI_VARIABLE = "FOUNDER_OS_CI"
WORKFLOW = ".github/workflows/check.yml"

# `make check` when the project has one, because that is the command the founder already
# maintains and the one CI runs. The doctor otherwise, which needs no project setup at
# all — a repository with no checks of its own still gets its gates proven.
HOOK_BODY = f"""#!/bin/sh
{MARKER}
# Runs the project's own checks before anything leaves this machine. Bypass with
# --no-verify when you genuinely need to push red work; that is a deliberate act and
# leaves a record, which a silently-skipped hosted run does not.
set -e

# Whatever pre-push was here before goes first, with the same stdin and arguments git
# gave us, and its refusal is still a refusal. Displacing someone's husky hook without
# running it would silently switch off a check they rely on.
_original="$(dirname "$0")/{DISPLACED_NAME}"
if [ -x "$_original" ]; then
    "$_original" "$@" || exit $?
fi

if [ -f Makefile ] && grep -q '^check:' Makefile; then
    exec make check
fi

if command -v founder-os-doctor >/dev/null 2>&1; then
    exec founder-os-doctor
fi

echo "founder-os: no 'make check' target and no doctor on PATH — nothing to run" >&2
exit 0
"""


def hooks_dir(ctx: GitContext) -> Path:
    """Honour core.hooksPath, or a repo that configured one gets a hook nothing reads.

    Worktrees share the common directory's hooks, which is what we want: the gate should
    not depend on which checkout the push happens from.
    """
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    ).stdout.strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else ctx.worktree_root / path
    return ctx.common_dir / "hooks"


def hook_path(ctx: GitContext) -> Path:
    return hooks_dir(ctx) / HOOK_NAME


def installed(ctx: GitContext) -> bool:
    try:
        return MARKER in hook_path(ctx).read_text(encoding="utf-8")
    except OSError:
        return False


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install(ctx: GitContext) -> tuple[bool, str]:
    """Put the hook in place, chaining any hook already there. Returns (changed, note)."""
    path = hook_path(ctx)
    if installed(ctx):
        return False, "pre-push hook already installed"

    displaced = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            # `path.exists()` follows symlinks and `write_text` writes THROUGH them, so a
            # hooks directory that symlinks pre-push at a script in the working tree —
            # husky and lefthook both do this — had our body written over that tracked
            # source file. `git status` showed the founder's own script modified, and the
            # undo could not put it back because it restored a hook, not the file.
            #
            # Move, never copy: the link itself is what has to go, so what remains is a
            # real file we own. `os.replace` moves a symlink as a symlink.
            target = path.parent / DISPLACED_NAME
            path.replace(target)
            with contextlib.suppress(OSError):
                _make_executable(target)
            displaced = target.name

        path.write_text(HOOK_BODY, encoding="utf-8")
        _make_executable(path)
    except OSError as exc:
        return False, f"could not install the pre-push hook: {exc}"

    if displaced:
        return True, (
            f"installed {path}\n"
            f"  Your existing {HOOK_NAME} was moved to {displaced} and now runs FIRST, "
            "before these checks. Nothing it used to refuse is allowed through."
        )
    return True, f"installed {path}"


def remove(ctx: GitContext) -> tuple[bool, str]:
    """Take the hook out and put back whatever was there before it."""
    path = hook_path(ctx)
    if not installed(ctx):
        return False, "no founder-os pre-push hook installed"

    path.unlink(missing_ok=True)

    displaced = path.parent / DISPLACED_NAME
    if displaced.is_symlink() or displaced.exists():
        displaced.replace(path)
        return True, f"removed, and put your original {HOOK_NAME} back"

    # The old shape, still honoured so an install from before the chaining change can be
    # undone by a plugin from after it.
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if backup.exists():
        path.write_text(backup.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        _make_executable(path)
        backup.unlink(missing_ok=True)
        return True, f"removed, and restored the previous {HOOK_NAME}"
    return True, "removed. Nothing checks your pushes from this machine now."


def workflow_state(ctx: GitContext) -> str:
    """`absent`, `gated` (opt-in, costs nothing) or `always` (runs on every push)."""
    path = ctx.worktree_root / WORKFLOW
    if not path.is_file():
        return "absent"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "absent"
    return "gated" if CI_VARIABLE in text else "always"


def hosted_enabled(ctx: GitContext) -> bool | None:
    """Whether the hosted workflow is switched on. None when it cannot be determined."""
    if not shutil.which("gh"):
        return None
    proc = subprocess.run(
        ["gh", "variable", "list", "--json", "name,value"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    if proc.returncode != 0:
        return None
    import json

    try:
        entries = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == CI_VARIABLE:
            return str(entry.get("value", "")).strip().lower() == "on"
    return False


def set_hosted(ctx: GitContext, on: bool) -> tuple[bool, str]:
    """Flip the repository variable the workflow is gated on."""
    if workflow_state(ctx) == "absent":
        return False, f"no {WORKFLOW} in this repository"
    if not shutil.which("gh"):
        return False, (
            f"gh is not installed, so set it by hand:\n"
            f"    gh variable set {CI_VARIABLE} --body {'on' if on else 'off'}\n"
            "or Settings -> Secrets and variables -> Actions -> Variables."
        )
    proc = subprocess.run(
        ["gh", "variable", "set", CI_VARIABLE, "--body", "on" if on else "off"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=120,
    )
    if proc.returncode != 0:
        return False, f"gh refused: {(proc.stderr or proc.stdout).strip()[:300]}"
    return True, f"hosted CI is now {'on' if on else 'off'} for this repository"


def status_lines(ctx: GitContext) -> list[str]:
    """What runs where, in the terms a founder cares about: cost and coverage."""
    local = installed(ctx)
    out = [f"local pre-push: {'ON — ' + str(hook_path(ctx)) if local else 'OFF'}"]

    state = workflow_state(ctx)
    if state == "absent":
        out.append("hosted CI:      no workflow in this repository")
    elif state == "always":
        out.append("hosted CI:      present and UNGATED — every push spends minutes")
    else:
        enabled = hosted_enabled(ctx)
        where = {True: "ON", False: "off", None: "unknown (gh unavailable)"}[enabled]
        out.append(f"hosted CI:      gated on {CI_VARIABLE}, currently {where}")

    if not local:
        out.append("")
        out.append("Nothing checks a push from this machine. `founder-os-ci local` fixes that.")
    return out
