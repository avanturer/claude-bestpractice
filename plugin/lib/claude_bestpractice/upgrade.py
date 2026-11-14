"""Whether the code running right now is the code that is installed.

Those are two different things, and the gap between them is silent. `claude plugin update`
answers with `Restart to apply changes.` and returns 0 — the new version is unpacked into a
sibling directory, the old one is marked `.orphaned_at` and left in place, and every session
already running keeps executing the old copy for as long as it lives. Nothing in the session
says so. A founder who updates to get a fix, watches the update succeed, and then watches the
fix not happen has no way to tell which of the two possible things went wrong.

Purely local: the version is the name of the directory this file is in, and the alternatives
are its siblings. No network, no manifest fetch, no cost when there is nothing to say. In a
development checkout there is no version directory and no siblings, so every function here
returns the empty answer and the caller prints nothing.

Since 2.19.0 the gap is closed where it can be (decision 0102): every command of an old copy
hands over to the newest one installed (`handover`), so a chat's gates run the version on disk
whenever the chat started. What Claude Code itself loaded at the chat's start, the skills and
commands it lists and the hooks module, waits for a new chat or a `/reload-plugins` the founder
types: Claude Code runs that command from their keyboard only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from . import MARKETPLACE, PLUGIN, __version__

# `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, written by the CLI. Matched
# rather than assumed: a checkout has a parent directory too, and it is not a version.
_VERSION_DIR = re.compile(r"^\d+(?:\.\d+)*(?:[-+].*)?$")

# Dropped by the CLI into the directory it has superseded, holding a millisecond timestamp.
ORPHAN_MARKER = ".orphaned_at"

# Set by `handover` for the copy it starts, naming the copy Claude Code loaded for the chat:
# the one its hook commands still point at. Present, the process is already the newest copy.
LOADED_FROM = "CLAUDE_BESTPRACTICE_LOADED_FROM"
# The founder's way back to each copy running its own code: `1` in the environment.
NO_HANDOVER = "CLAUDE_BESTPRACTICE_NO_HANDOVER"
# Where the hook's process can become the newer copy's; Windows has no such `execve`.
EXECVE = os.name == "posix"


# Versions that shipped a defect serious enough that staying on one is not a choice
# somebody would make knowingly. A released version cannot be withdrawn — a tag is
# permanent and `claude plugin` will happily keep serving it — so the only place this can
# be said is inside the plugin, to the person running it.
#
# Kept short and kept honest: a version belongs here when it BROKE something that worked,
# not merely when a later one improved on it. Everything is superseded eventually; that is
# not a defect.
KNOWN_BAD = {
    "1.0.13": "`make check` cannot run under a virtualenv, which blocks pushing (#50)",
    "1.0.11": "sessions cannot see each other; file leases never hold (#43)",
    "1.0.12": "sessions cannot see each other; file leases never hold (#43)",
}


def known_bad(version: str | None = None) -> str:
    """Why the running version should not be stayed on, or empty when it is fine."""
    reason = KNOWN_BAD.get(version or __version__, "")
    return (
        f"claude-bestpractice {version or __version__} has a known defect: {reason}. "
        f"Update with `{update_command()}`."
        if reason else ""
    )


def version_key(name: str) -> tuple:
    """Sortable, and total. `1.0.10` must outrank `1.0.9`, and a build suffix must not
    make the comparison throw — an unparseable component sorts below every number, which
    keeps a prerelease behind the release it precedes rather than ahead of it."""
    parts = []
    for chunk in re.split(r"[.\-+]", name):
        parts.append((1, int(chunk), "") if chunk.isdigit() else (0, 0, chunk))
    return tuple(parts)


def install_root(start: Path | None = None) -> Path | None:
    """The directory the CLI unpacked this copy into, or None in a checkout.

    `start` is how the tests put a real directory layout under this without needing the
    module to have been imported from inside one.
    """
    for parent in (start or Path(__file__).resolve()).parents:
        if (parent / ".claude-plugin" / "plugin.json").is_file():
            return parent
    return None


def installed_copy(root: Path | None = None) -> bool:
    """Whether this code runs from a version directory the CLI unpacked, not a checkout."""
    root = root or install_root()
    return bool(root and _VERSION_DIR.match(root.name))


def superseded_by(root: Path | None = None) -> str | None:
    """A newer version sitting beside this one, meaning this session is running stale code.

    The founder has already done everything asked of them at this point: they ran the
    update and it worked. What is left is a restart, and this is the only thing that can
    tell them so.
    """
    root = root or install_root()
    if root is None or not _VERSION_DIR.match(root.name):
        return None
    try:
        siblings = [
            entry.name
            for entry in root.parent.iterdir()
            if entry.is_dir() and _VERSION_DIR.match(entry.name)
        ]
    except OSError:
        return None

    newer = [name for name in siblings if version_key(name) > version_key(root.name)]
    return max(newer, key=version_key) if newer else None


def newest_install(root: Path | None = None) -> Path | None:
    """The newest copy the CLI has unpacked beside this one, when it is newer and the CLI has
    not left it behind; None otherwise.

    A copy marked `.orphaned_at` is one the CLI moved off, which is what a downgrade does to
    the newer version: handing over to it would undo the founder's choice. A directory with no
    manifest is one still being unpacked.
    """
    root = root or install_root()
    if root is None or not _VERSION_DIR.match(root.name):
        return None
    try:
        newer = [entry for entry in root.parent.iterdir()
                 if entry.is_dir() and _VERSION_DIR.match(entry.name)
                 and version_key(entry.name) > version_key(root.name)
                 and not (entry / ORPHAN_MARKER).exists()
                 and (entry / ".claude-plugin" / "plugin.json").is_file()]
    except OSError:
        return None
    return max(newer, key=lambda entry: version_key(entry.name), default=None)


def handover(script: Path) -> None:
    """Run the newest installed copy's namesake of `script` in this process's place and end
    with its status; return, doing nothing, where there is none.

    A chat Claude Code started before an update keeps running the hook commands of the copy
    it loaded until it is reloaded, and `/reload-plugins` runs only when the founder types
    it: asked by a plugin it answers "/reload-plugins isn't available over a remote
    connection in this session", measured on 2.1.325. So each command of an old copy hands
    over. The newer copy gets this process's stdin, stdout and stderr untouched, the same
    arguments, `CLAUDE_PLUGIN_ROOT` naming itself, and `LOADED_FROM` naming this copy.
    Replaced with `execve` where the platform has it, so the hook's process is the newer
    copy's and a timeout stops that; on Windows run as a child, its status passed on.
    """
    found = _handover_target(script)
    if found is None:
        return
    loaded, newer, target = found
    argv = [sys.executable, str(target), *sys.argv[1:]]
    env = {**os.environ, LOADED_FROM: str(loaded), "CLAUDE_PLUGIN_ROOT": str(newer)}
    try:
        if EXECVE:
            os.execve(sys.executable, argv, env)
        code = subprocess.run(argv, env=env, check=False).returncode
    except OSError:
        return
    sys.exit(code)


def _handover_target(script: Path) -> tuple[Path, Path, Path] | None:
    """(the copy `script` is in, the newest installed copy, its namesake of `script`), or None
    where nothing is to be handed over: already handed over, switched off, a checkout, the
    newest copy, or a newer copy without that command."""
    if os.environ.get(LOADED_FROM) or os.environ.get(NO_HANDOVER) == "1":
        return None
    loaded = install_root(script.resolve())
    newer = newest_install(loaded) if loaded else None
    target = newer / "bin" / script.name if newer else None
    return (loaded, newer, target) if loaded and newer and target and target.is_file() else None


def loaded_version() -> str:
    """The version Claude Code loaded for this chat, when a handover started this process
    from an older copy; empty otherwise."""
    loaded = os.environ.get(LOADED_FROM, "")
    return Path(loaded).name if loaded and _VERSION_DIR.match(Path(loaded).name) else ""


def orphaned(root: Path | None = None) -> bool:
    """This copy has been superseded and left behind, stated by the CLI itself."""
    root = root or install_root()
    return bool(root and (root / ORPHAN_MARKER).exists())


# Where the CLI unpacks every marketplace install, whatever marketplace it came from.
CACHE = Path(".claude") / "plugins" / "cache"


def newer_in_the_cache(home: Path | None = None) -> str | None:
    """A newer version unpacked anywhere in the CLI's cache, or None.

    `superseded_by` only sees SIBLINGS of the running copy, so it answers nothing at all
    for a copy that is not itself in a version directory — which is every `install.sh`
    install, because those run from a git checkout. That founder updates through the
    marketplace, the CLI unpacks a new version somewhere else entirely, the session keeps
    running the checkout, and the board stays silent because the only detector it had was
    looking at the wrong place. Reported as a session insisting it was 1.17.0 the day after
    an update and a full machine restart.

    Offline by construction: it reads directories the CLI already wrote.
    """
    root = (home or Path.home()) / CACHE
    try:
        # The glob does the filtering that a name check used to: whichever marketplace it
        # came from, only OUR plugin's directory is opened. Under its current name only:
        # the CLI never unpacks a version past 1.73.0 under `claude-bestpractice/`.
        cached = [
            entry.name
            for plugin_dir in root.glob(f"*/{PLUGIN}")
            for entry in plugin_dir.iterdir()
            if entry.is_dir() and _VERSION_DIR.match(entry.name)
        ]
    except OSError:
        return None
    newer = [name for name in cached if version_key(name) > version_key(__version__)]
    return max(newer, key=version_key) if newer else None


def stale_line(root: Path | None = None, home: Path | None = None) -> str:
    """One line for the board, and only when it is true. Empty is the normal answer.

    Never a guess: this fires when a newer copy is on disk, not when one might exist
    upstream. Checking upstream would need the network on every session start, and a
    session start that waits on github is a worse product than one that misses a release.

    Two places are searched, because there are two install shapes. `root` covers a
    marketplace install, where the newer version is a sibling directory. `home` covers the
    rest of the CLI's cache, which is the only thing that can see a newer version when
    this copy is a git checkout and has no version directory to have siblings in.
    """
    loaded = loaded_version()
    if loaded:
        return (
            f"\nthis chat was started on claude-bestpractice {loaded}; its gates run "
            f"{__version__}, the copy installed since. What {__version__} adds to the skills, "
            "the commands and the board pane reaches it in a new chat, or after `/reload-plugins` "
            "typed by the founder: Claude Code takes that command from their keyboard only."
        )
    root = root or install_root()
    newer = superseded_by(root) or newer_in_the_cache(home)
    if newer:
        return (
            f"\nthis session is running claude-bestpractice {__version__}, but {newer} is "
            "installed on disk. A new chat, or `/reload-plugins` typed by the founder, picks "
            "it up; nothing here is enforcing the newer version's rules until then."
        )
    if orphaned(root):
        return (
            f"\nthis session is running claude-bestpractice {__version__} from a copy the "
            "CLI has superseded. A new chat, or `/reload-plugins` typed by the founder, picks "
            "up the copy that replaced it."
        )
    return ""


def update_command() -> str:
    """The command that works, qualified, because the short form has not always worked.

    `claude plugin update claude-bestpractice` failed with `Plugin not found` while the
    plugin was installed and enabled, which reads as a broken install rather than a wrong
    argument. 2.1.289 takes the short form too; the qualified one works on both.
    """
    return f"claude plugin update {PLUGIN}@{MARKETPLACE}"
