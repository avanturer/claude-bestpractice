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
"""

from __future__ import annotations

import re
from pathlib import Path

from . import __version__

# `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, written by the CLI. Matched
# rather than assumed: a checkout has a parent directory too, and it is not a version.
_VERSION_DIR = re.compile(r"^\d+(?:\.\d+)*(?:[-+].*)?$")

# Dropped by the CLI into the directory it has superseded, holding a millisecond timestamp.
ORPHAN_MARKER = ".orphaned_at"


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
        "Update with `claude plugin update claude-bestpractice`."
        if reason else ""
    )


def _key(name: str) -> tuple:
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

    newer = [name for name in siblings if _key(name) > _key(root.name)]
    return max(newer, key=_key) if newer else None


def orphaned(root: Path | None = None) -> bool:
    """This copy has been superseded and left behind, stated by the CLI itself."""
    root = root or install_root()
    return bool(root and (root / ORPHAN_MARKER).exists())


def stale_line(root: Path | None = None) -> str:
    """One line for the board, and only when it is true. Empty is the normal answer.

    Never a guess: this fires when a newer copy is on disk, not when one might exist
    upstream. Checking upstream would need the network on every session start, and a
    session start that waits on github is a worse product than one that misses a release.
    """
    root = root or install_root()
    newer = superseded_by(root)
    if newer:
        return (
            f"\nthis session is running claude-bestpractice {__version__}, but {newer} is "
            "installed on disk. Restart Claude Code to pick it up; nothing here is enforcing "
            "the newer version's rules until you do."
        )
    if orphaned(root):
        return (
            f"\nthis session is running claude-bestpractice {__version__} from a copy the "
            "CLI has superseded. Restart Claude Code."
        )
    return ""


def update_command() -> str:
    """The command that works, qualified, because the short form does not.

    `claude plugin update claude-bestpractice` fails with `Plugin not found` while the
    plugin is installed and enabled, which reads as a broken install rather than a wrong
    argument. `install` accepts the short name; `update` does not.
    """
    return "claude plugin update claude-bestpractice@claude-bestpractice"
