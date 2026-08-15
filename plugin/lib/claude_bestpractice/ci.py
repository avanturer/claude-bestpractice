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
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .gitctx import GitContext

HOOK_NAME = "pre-push"
MARKER = "# claude-bestpractice pre-push gate"

# The hook body is written once and then never again: `ensure` skips the moment it finds
# one installed. So every fix to the hook — and v1.0.0 shipped a serious one, an `exit 0`
# where a project WITH a suite pushed with nothing run — reached new repositories only.
# Anyone who had already used the plugin kept the buggy hook forever, and had no way to
# know. Updating the plugin has to update what the plugin installed.
#
# Stamped rather than compared byte-for-byte: the body embeds this project's detected test
# command, so two correct hooks legitimately differ, and a content check would rewrite the
# founder's hook on every session start.
STAMP = "# claude-bestpractice hook version:"
BACKUP_SUFFIX = ".claude-bestpractice.bak"

# A hook we displaced is moved here and CHAINED, never merely copied aside. Copying it
# aside is what "backup" sounds like and is not what the founder needs: their husky or
# lefthook pre-push stopped running the moment ours landed, silently, and a check that
# stopped running is the exact failure this project exists to prevent — committed by the
# thing that prevents it.
DISPLACED_NAME = "pre-push.claude-bestpractice-original"

# What this project called itself before it was renamed. A hook written then is OURS —
# it just says so in the old words — and every routine here recognised only the current
# marker, so it was never upgraded and never could be. It predates both the tree-hash
# short circuit and the green-run recorder, which is why the optimisation shipped in
# v1.27.0 had, in the repository that wrote it, never once fired (#146).
FOUNDER_OS_MARKER = "# founder-os pre-push gate"
FOUNDER_OS_DISPLACED_NAME = "pre-push.founder-os-original"

# Set by `claude-bp-ci off`, read by `ensure`. Its whole job is to make an opt-out stick
# across the session start that would otherwise put the hook straight back.
DECLINED_NAME = "pre-push-declined"
CI_VARIABLE = "CLAUDE_BESTPRACTICE_CI"
WORKFLOW = ".github/workflows/check.yml"

# `make check` when the project has one, because that is the command the founder already
# maintains and the one CI runs. The doctor otherwise, which needs no project setup at
# all — a repository with no checks of its own still gets its gates proven.
HOOK_TEMPLATE = f"""#!/bin/sh
{MARKER}
{STAMP} __VERSION__
# Runs this project's own checks before anything leaves the machine. Bypass with
# --no-verify when you genuinely need to push red work; that is a deliberate act and
# leaves a record, which a silently-skipped hosted run does not.
set -e

# A pre-push hook that was already here runs first, with the same stdin and arguments
# git gave us, and its refusal is still a refusal. Displacing a husky or lefthook hook
# without running it would switch off a check you rely on.
_original="$(dirname "$0")/{DISPLACED_NAME}"
if [ -x "$_original" ]; then
    "$_original" "$@" || exit $?
fi

# Already proven on THIS EXACT TREE, so running it again cannot change the answer. The
# stamp is the tree hash, not a timestamp: a dirty tree stamps to nothing and a single
# edit changes it, so the only thing this can skip is work already done. Five minutes a
# push, for an answer that could not differ.
if __GREEN_COVERS__ >/dev/null 2>&1; then
    exit 0
fi

if [ -f Makefile ] && grep -q '^check:' Makefile; then
    make check || exit $?
    __RECORD_GREEN__ 'make check' >/dev/null 2>&1 || true
    exit 0
fi

__TEST_COMMAND__

# Nothing was baked in above, so ask the plugin directly. This resolves only when
# claude-bp is on your shell PATH — install.sh arranges that, the marketplace install
# does not, which is why the tier above exists at all.
_cmd="$(claude-bp-ci --print-test-command 2>/dev/null || true)"
if [ -n "$_cmd" ]; then
    sh -c "$_cmd" || exit $?
    __RECORD_GREEN__ "$_cmd" >/dev/null 2>&1 || true
fi

# There used to be a `claude-bp-doctor` tier here, and it was wrong twice over. Proving
# THIS PLUGIN's gates fire is not evidence about the code being pushed, so a push of
# healthy code was rejected whenever the doctor tripped on the environment, and ~40s of
# self-test ran in place of anything belonging to the repository. And because a
# marketplace install puts the plugin's `bin/` on PATH, the tier fired exactly for the
# people who use this — while the test asserting the honest "nothing to run" line could
# only pass for someone who does not. CI was green for that reason.
#
# In this repository it closed a loop: pre-push found `check:`, `make check` was red
# inside a session for the reason above, so claude-bestpractice refused to let
# claude-bestpractice be pushed from a Claude Code session. Reported as issue #30.

# Allowed, and the reason is a true statement rather than a swallowed failure: this
# project has no `make check` target and had no test runner to detect.
echo "claude-bestpractice: nothing to run — no 'make check' target and no test runner" >&2
echo "was detectable here. Run 'claude-bp-ci local' once this project has a suite." >&2
exit 0
"""

# Rendered into __TEST_COMMAND__ when a runner WAS detected. The refusal at the end is
# the point: reaching it means this project has a suite that could not be run, and
# falling through to the tiers below would let the push go out reported as checked while
# nothing checked it. A gate that cannot verify must not pretend it did.
DETECTED_TIER = """# This project's own suite, detected when the hook was installed. It is baked in
# rather than resolved at push time because git hands a hook a stripped environment
# in which claude-bp is usually not on PATH. Re-run `claude-bp-ci` if the runner changes.
_runner={runner}
if command -v "$_runner" >/dev/null 2>&1; then
    # NOT `exec`. It replaced the shell, which was harmless while passing or failing was
    # this hook's only job — and silently dropped the second job the moment it had one.
    # #84 added recording to the two literal tiers of the template and never reached this
    # one, which is generated here and is the tier that fires for any project with a
    # detected runner and no `check:` target. That is most of them (#87).
    {command} || exit $?
    __RECORD_GREEN__ {quoted} >/dev/null 2>&1 || true
    exit 0
fi

echo "claude-bestpractice: $_runner is not on PATH, so this project's suite could not" >&2
echo "run. Refusing the push rather than reporting a check that never happened. Fix the" >&2
echo "environment, run 'claude-bp-ci local' if the runner changed, or push with --no-verify." >&2
exit 1"""

NO_RUNNER_TIER = "# (no test runner was detectable in this project at install time)"


def _recorder() -> str:
    """The command that records a green pre-push run, with absolute paths baked in.

    A green run this plugin executed itself is evidence by the same standard the Stop gate
    uses (decision 0004) — the plugin ran the project's declared command and saw the exit
    code. It was being thrown away: `record_green` was reachable only from the Stop gate,
    which writes for the branch of the tree the SESSION occupies, so a suite run in the
    branch's own worktree, and the one this very hook runs on every push, counted for
    nothing. The session that merges stands in the main checkout by this plugin's own
    design, so the last merge blocker asked it for evidence it could not produce (#83).

    Absolute, because a git hook runs with a stripped environment and cannot rely on
    `claude-bp-ci` being on PATH — the same reason the test command is baked rather than
    resolved. Failure is swallowed: recording is bookkeeping, and a push that passed its
    checks must never be refused because the bookkeeping did not land.
    """
    import sys
    from shlex import quote

    recorder = Path(__file__).resolve().parent.parent.parent / "bin" / "claude-bp-ci"
    return f"{quote(sys.executable)} {quote(str(recorder))} record-green"


def _green_check() -> str:
    """The command that answers whether this exact tree was already proven green.

    Absolute for the same reason as `_recorder`, and its FAILURE is the safe answer: the
    hook only skips when this exits 0, so a missing interpreter, a stripped environment or
    an unreadable record all mean the suite runs.
    """
    import sys
    from shlex import quote

    checker = Path(__file__).resolve().parent.parent.parent / "bin" / "claude-bp-ci"
    return f"{quote(sys.executable)} {quote(str(checker))} green-covers-tree"


def hook_body(ctx: GitContext | None = None) -> str:
    """The hook script, with this project's own test command baked into the middle tier.

    Baked at install time rather than resolved at push time on purpose: a git hook runs
    with a stripped environment and cannot rely on `claude-bp` being on PATH, and a hook
    that silently finds nothing to run is the failure this whole module exists to avoid.
    The cost is that it goes stale if the project changes runner, which `claude-bp-ci`
    fixes and the comment in the script says so.
    """
    from shlex import quote

    command = []
    if ctx is not None:
        from .config import detect_test_command

        command = detect_test_command(ctx.worktree_root)

    from . import __version__

    rendered = NO_RUNNER_TIER
    if command:
        joined = " ".join(quote(part) for part in command)
        rendered = DETECTED_TIER.format(
            runner=quote(command[0]),
            command=joined,
            quoted=quote(joined),
        )
    body = HOOK_TEMPLATE.replace("__TEST_COMMAND__", rendered)
    body = body.replace("__RECORD_GREEN__", _recorder())
    body = body.replace("__GREEN_COVERS__", _green_check())
    return body.replace("__VERSION__", __version__)


def stamped_version(ctx: GitContext) -> str:
    """The plugin version that wrote the installed hook, or "" when there is none."""
    try:
        text = hook_path(ctx).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith(STAMP):
            return line[len(STAMP):].strip()
    # Ours, but from before stamping existed — in either spelling. Any stamped version
    # is newer than that, so "0" is what makes `refresh()` rewrite it.
    return "0" if (MARKER in text or FOUNDER_OS_MARKER in text) else ""


def refresh(ctx: GitContext) -> bool:
    """Rewrite our own hook in place when it was written by an older plugin.

    In place, and only over our own file: `install()` displaces whatever was at this path
    into `pre-push.claude-bestpractice-original` and chains it. Reusing that path here
    would move OUR hook onto theirs and destroy the founder's husky or lefthook script —
    the one thing this module has always refused to do.
    """
    from . import __version__

    current = stamped_version(ctx)
    if not current or current == __version__:
        return False
    _carry_the_displaced_hook(ctx)
    try:
        hook_path(ctx).write_text(hook_body(ctx), encoding="utf-8")
        _make_executable(hook_path(ctx))
    except OSError:
        return False
    return True


def _carry_the_displaced_hook(ctx: GitContext) -> None:
    """Move a hook the OLD name displaced to where the new body looks for it.

    The old body chained `pre-push.founder-os-original`; the new one chains
    `pre-push.claude-bestpractice-original`. Rewriting the body without moving the file
    would leave a husky or lefthook hook on disk, unreferenced and silently not running —
    the one failure this module has always refused to cause, arriving through the repair
    meant to prevent it.

    Never overwrites: an existing file under the new name is the current arrangement and
    is left alone.
    """
    old = hooks_dir(ctx) / FOUNDER_OS_DISPLACED_NAME
    new = hooks_dir(ctx) / DISPLACED_NAME
    with contextlib.suppress(OSError):
        if old.exists() and not new.exists():
            old.rename(new)


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
    """Ours, in either spelling of our name.

    The old spelling has to count, or `ensure()` treats our own hook as a stranger's and
    DISPLACES it — chaining `exec make check` in front of a body that then runs the suite
    a second time. Worse than the staleness it was trying to fix.
    """
    try:
        text = hook_path(ctx).read_text(encoding="utf-8")
    except OSError:
        return False
    return MARKER in text or FOUNDER_OS_MARKER in text


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_new_file(path: Path, body: str) -> None:
    """Create the hook, refusing to follow a symlink that appeared under us.

    The displacement above already moves any existing entry aside, so by the time we
    get here the path should be free — but `write_text` follows a symlink, and a
    symlink is precisely what husky and lefthook leave at this path. O_EXCL|O_NOFOLLOW
    turns "something raced us, or the displacement missed a case" into an error
    instead of into a founder's tracked source file being overwritten in place.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o755)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    _make_executable(path)


def _declined_path(ctx: GitContext):
    from .store import tier_b

    return tier_b(ctx, DECLINED_NAME)


def declined(ctx: GitContext) -> bool:
    """Did a human take this hook out on purpose?

    Tier B, because that is exactly the lifetime of the thing it describes: a git hook
    is per-clone and never committed, and so is the decision to be rid of one. Recording
    it in Tier A would push one machine's opt-out onto every other checkout of the branch.
    """
    try:
        return _declined_path(ctx).exists()
    except OSError:
        return False


def ensure(ctx: GitContext) -> tuple[bool, str]:
    """Arm the gate if it is absent and nobody declined it. Returns (changed, note).

    `Setup` fires on `--init`, so the hook was installed only in repositories that were
    initialised through the plugin. Install it into an existing project the ordinary way
    — `/plugin install`, which is the way the README leads with — and `Setup` never
    fires: `claude plugin list` says enabled, the board renders, gates fire in-session,
    and nothing whatsoever guards a push. That is the failure this module's own docstring
    calls worse than no gate, shipped by default.

    SessionStart is the event that reliably fires, so it is the one that arms this. The
    work is skipped outright once the hook is there, which is every session after the
    first, and the create is O_EXCL, so eight sessions starting at once produce one hook
    and seven no-ops rather than a torn file.
    """
    if installed(ctx):
        # Installed, but possibly by an older plugin. An upgrade that fixes the hook has to
        # reach the repositories that already have one, or the fix ships to nobody who was
        # already using it.
        return (True, "refreshed") if refresh(ctx) else (False, "")
    if declined(ctx):
        return False, ""
    return install(ctx)


def install(ctx: GitContext) -> tuple[bool, str]:
    """Put the hook in place, chaining any hook already there. Returns (changed, note)."""
    path = hook_path(ctx)
    # Asking for it back is consent, and it has to clear the opt-out or `claude-bp-ci local`
    # would appear to work and be undone by the next session start.
    with contextlib.suppress(OSError):
        _declined_path(ctx).unlink(missing_ok=True)
    if installed(ctx):
        # Installed is not current. `ensure()` has upgraded a stale hook since #33; this
        # path predates it and short-circuited on existence, so the one command whose whole
        # purpose is "run the checks locally" was the one that declined to update the
        # checks — and a founder who ran it after an upgrade reasonably believed they now
        # had the shipped gate. They had whatever their last session start wrote (#85).
        #
        # Not routed through `ensure()`, which honours the opt-out: asking for the hook
        # back is consent, and that is cleared just above.
        from . import __version__

        before = stamped_version(ctx) or "unknown"
        if refresh(ctx):
            return True, f"pre-push hook updated {before} -> {__version__}"
        return False, f"pre-push hook already current ({__version__})"

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

        _write_new_file(path, hook_body(ctx))
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

    # Recorded before the unlink, and recorded even when there was nothing to remove, so
    # that `off` means "stay off". Without this, SessionStart re-arms what the founder
    # just switched off and the only way to keep it off is to keep running `off` — which
    # is not a tool obeying its owner, it is a tool arguing with them.
    with contextlib.suppress(OSError):
        from . import store

        store.ensure_dir(_declined_path(ctx).parent)
        _declined_path(ctx).write_text("", encoding="utf-8")

    if not installed(ctx):
        return False, "no claude-bestpractice pre-push hook installed"

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


def _foreign_workflows(ctx: GitContext) -> int:
    """How many workflows this repository has that are not the one we ship."""
    directory = ctx.worktree_root / ".github" / "workflows"
    try:
        return sum(
            1
            for entry in directory.iterdir()
            if entry.is_file()
            and entry.suffix in (".yml", ".yaml")
            and entry.name != Path(WORKFLOW).name
        )
    except OSError:
        return 0


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
        # "no workflow in this repository" was a claim about the repository made from a
        # test for one specific file. A repo with four workflows of its own was told it
        # had none, one line under `stage: … CI config present` — two lines of the same
        # output contradicting each other, and the wrong one sounded authoritative.
        others = _foreign_workflows(ctx)
        if others:
            out.append(
                f"hosted CI:      {others} workflow(s) of your own, none of them ours — "
                "`claude-bp-ci github` adds one"
            )
        else:
            out.append("hosted CI:      no workflow in this repository")
    elif state == "always":
        out.append("hosted CI:      present and UNGATED — every push spends minutes")
    else:
        enabled = hosted_enabled(ctx)
        where = {True: "ON", False: "off", None: "unknown (gh unavailable)"}[enabled]
        out.append(f"hosted CI:      gated on {CI_VARIABLE}, currently {where}")

    if not local:
        out.append("")
        out.append("Nothing checks a push from this machine. `claude-bp-ci local` fixes that.")
    return out
