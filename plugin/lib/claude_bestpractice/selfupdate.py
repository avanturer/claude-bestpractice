"""The plugin brings its own updates to disk, and `curated` with them (decision 0102).

Claude Code updates a marketplace that is not Anthropic's only once its auto-update is turned
on, and installs a plugin only when told to. The founder asked for neither step:

> я не хочу сам ничего выполнять все должно было обновиться в этом чате и всем соседним
> активным чатам

So a session's start, at most once in `INTERVAL` on this machine, sets off a detached process
that asks the CLI to refresh this marketplace, to update every plugin installed from it at a
scope this session can name, and to install `curated` where it never was. A chat already
running then gates on the new copy through `upgrade.handover`; what Claude Code loaded for it
itself waits for a new chat.

Nothing runs where Claude Code's own switches turn updates off (`DISABLE_AUTOUPDATER`,
`DISABLE_UPDATES`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` without
`FORCE_AUTOUPDATE_PLUGINS`), where the founder set this marketplace's `autoUpdate` to false,
where `CLAUDE_BESTPRACTICE_NO_SELF_UPDATE=1`, from a development checkout, or with no `claude`
on the PATH. `curated` removed after it was installed here is not installed again.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import MARKETPLACE, PLUGIN, store, upgrade

INTERVAL = 6 * 3600
# One CLI call that takes longer than this is waited on no further; the next start retries.
CALL_TIMEOUT = 300
CURATED = f"curated@{MARKETPLACE}"
NO_SELF_UPDATE = "CLAUDE_BESTPRACTICE_NO_SELF_UPDATE"
STATE = "self-update.json"
INSTALLED = Path(".claude") / "plugins" / "installed_plugins.json"


def data_dir(home: Path | None = None) -> Path:
    """The plugin's directory that outlives its versions: `CLAUDE_PLUGIN_DATA`, which Claude
    Code sets for a plugin's hooks, else where it puts that directory."""
    named = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(named) if named else (
        (home or Path.home()) / ".claude" / "plugins" / "data" / f"{PLUGIN}-{MARKETPLACE}")


# Claude Code's own switches that stop plugin updates, unless `FORCE_AUTOUPDATE_PLUGINS=1`.
CLAUDE_CODE_OFF = ("DISABLE_AUTOUPDATER", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")


def off(home: Path | None = None) -> str:
    """Why no update runs here, or empty when one may."""
    if os.environ.get(NO_SELF_UPDATE) == "1":
        return f"{NO_SELF_UPDATE}=1"
    forced = os.environ.get("FORCE_AUTOUPDATE_PLUGINS") == "1"
    switched = next((name for name in CLAUDE_CODE_OFF if os.environ.get(name)), "")
    if switched and not forced:
        return switched
    return _marketplace_off(home)


def _marketplace_off(home: Path | None) -> str:
    """Why the founder's settings keep this marketplace from updating, or empty."""
    settings = store.read_json((home or Path.home()) / ".claude" / "settings.json", default={})
    known = settings.get("extraKnownMarketplaces") if isinstance(settings, dict) else None
    entry = known.get(MARKETPLACE) if isinstance(known, dict) else None
    off_here = isinstance(entry, dict) and entry.get("autoUpdate") is False
    return f"extraKnownMarketplaces.{MARKETPLACE}.autoUpdate is false" if off_here else ""


def installed(home: Path | None = None) -> dict[str, list[dict]]:
    """Each plugin of this marketplace the CLI has installed, with its install entries."""
    data = store.read_json((home or Path.home()) / INSTALLED, default={})
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return {name: [entry for entry in entries if isinstance(entry, dict)]
            for name, entries in (plugins.items() if isinstance(plugins, dict) else ())
            if name.endswith(f"@{MARKETPLACE}") and isinstance(entries, list)}


def plan(projects: list[Path], state: dict, home: Path | None = None) -> list[tuple[list[str], Path | None]]:
    """The CLI calls to make, in order, each with the directory it runs in.

    A plugin installed for a project is updated only from that project, which is the one
    place `--scope project` finds it; a project this session is not in waits for a session
    there. `curated` is installed for the user, once.
    """
    here = {str(path.resolve()) for path in projects}
    found = installed(home)
    calls = [(["plugin", "marketplace", "update", MARKETPLACE], None)]
    calls += [call for name, entries in sorted(found.items())
              for entry in entries if (call := _update(name, entry, here))]
    if CURATED not in found and not state.get("curated_installed_at"):
        calls.append((["plugin", "install", CURATED, "--scope", "user"], None))
    return calls


def _update(name: str, entry: dict, here: set[str]) -> tuple[list[str], Path | None] | None:
    """The call that updates one install of `name`, or None for a project this session is
    not in."""
    scope, project = entry.get("scope"), str(entry.get("projectPath") or "")
    if scope == "user":
        return ["plugin", "update", name, "--scope", "user"], None
    if scope in ("project", "local") and project and str(Path(project).resolve()) in here:
        return ["plugin", "update", name, "--scope", scope], Path(project)
    return None


def due(home: Path | None = None, now: float | None = None) -> bool:
    """Whether a run may start now, claimed for this process when it may: the last run's
    start is stamped under the lock, so two sessions starting together run one."""
    path = data_dir(home) / STATE
    with store.file_lock(path.with_suffix(".lock")):
        state = store.read_json(path, default={})
        state = state if isinstance(state, dict) else {}
        moment = time.time() if now is None else now
        if moment - float(state.get("started_at") or 0) < INTERVAL:
            return False
        store.write_json(path, {**state, "started_at": moment})
    return True


def start(projects: list[Path], home: Path | None = None) -> None:
    """Set off a run in a process nobody waits on, when one is allowed and due.

    Only from a copy the CLI installed: a development checkout has no version directory, and
    an update run from it would replace what the founder is testing.
    """
    if not upgrade.installed_copy() or off(home) or shutil.which("claude") is None \
            or not due(home):
        return
    library = Path(__file__).resolve().parent.parent
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
            "from claude_bestpractice import selfupdate; "
            "selfupdate.run([Path(p) for p in sys.argv[2:]])")
    with contextlib.suppress(OSError):
        subprocess.Popen(
            [sys.executable, "-c", code, str(library), *map(str, projects)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def run(projects: list[Path], home: Path | None = None) -> list[dict]:
    """Make the calls `plan` names and keep what each answered; the record of a run."""
    claude = shutil.which("claude")
    path = data_dir(home) / STATE
    state = store.read_json(path, default={})
    state = state if isinstance(state, dict) else {}
    ran = [_call(claude, argv, cwd) for argv, cwd in plan(projects, state, home)] if claude else []
    with store.file_lock(path.with_suffix(".lock")):
        state = store.read_json(path, default={})
        state = state if isinstance(state, dict) else {}
        if any(r["exit"] == 0 and r["call"].startswith(f"plugin install {CURATED}") for r in ran):
            state["curated_installed_at"] = time.time()
        store.write_json(path, {**state, "finished_at": time.time(), "ran": ran, "told": False})
    return ran


def _call(claude: str, argv: list[str], cwd: Path | None) -> dict:
    """One CLI call made, and what it answered."""
    try:
        proc = subprocess.run([claude, *argv], cwd=str(cwd) if cwd else None,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=CALL_TIMEOUT, check=False)
        said, code = (proc.stdout + proc.stderr).strip(), proc.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        said, code = str(exc), -1
    return {"call": " ".join(argv), "exit": code, "said": said[-400:]}


def line(home: Path | None = None) -> str:
    """What the last run brought, once: the plugins it updated or installed. Empty when it
    brought nothing, or was already told."""
    path = data_dir(home) / STATE
    with store.file_lock(path.with_suffix(".lock")):
        state = store.read_json(path, default={})
        if not isinstance(state, dict) or state.get("told", True):
            return ""
        store.write_json(path, {**state, "told": True})
    brought = [r["call"].split()[2] for r in state.get("ran") or []
               if isinstance(r, dict) and r.get("exit") == 0
               and ("updated from" in str(r.get("said")) or "Successfully installed" in str(r.get("said")))]
    return f"\nclaude-bestpractice updated itself: {', '.join(brought)}." if brought else ""


def summary(home: Path | None = None) -> str:
    """The last run, for `claude-bp status`: when it finished and each call's exit."""
    state = store.read_json(data_dir(home) / STATE, default={})
    if not isinstance(state, dict) or not state.get("finished_at"):
        return f"self-update: none run yet{f' ({off(home)})' if off(home) else ''}"
    calls = ", ".join(f"{r.get('call')} → {r.get('exit')}" for r in state.get("ran") or [])
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(state["finished_at"])))
    return f"self-update: {when}: {calls or 'nothing to run'}"
