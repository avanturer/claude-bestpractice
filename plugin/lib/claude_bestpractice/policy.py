"""The half of the permission policy that can be derived, and who holds the pen on it.

Two layers answer the same question — may this call proceed unattended. Claude Code's
auto mode answers it from prose in `~/.claude/settings.json`; this plugin answers it from
state it computes on every hook call. The only thing joining them was the founder retyping
one half into the other: 8,940 bytes of hand-written policy on one machine, most of it
authored mid-session, at the moment a prompt had already interrupted something else (#113).

The line drawn here is between a FACT and a GRANT.

**Facts are generated.** Where this repository is, what its remotes are, what its trunk is
called, what its checks are, that several sessions share one clone through worktrees under
`.claude/worktrees/`. Every one is re-derivable from the repository, so nothing an agent
says can change what gets written — which is what makes it safe for an agent to run the
command rather than the founder.

**Grants are not.** `autoMode.allow` widens what may proceed unattended, and a session
that has just been interrupted has a direct motive to widen it. That stays hand-written,
and this module never adds a line to it.

**Nothing hand-written is ever touched.** Every generated entry carries a marker naming
this repository, and only entries carrying it are read, replaced or removed. A rule the
founder wrote survives verbatim, including one this module would have written differently.
Rules that have gone dead or stale are REPORTED — the same answer decision 0007 gives about
their instruction files, for the same reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .gitctx import GitContext

SETTINGS = ".claude/settings.json"

# Prefix on every generated entry. The repository path is IN the marker because this file
# governs every repository on the machine while the plugin is installed per project: two
# governed repositories must produce two blocks that can be regenerated independently, and
# a rule about one must never be dropped while refreshing the other.
MARK = "[claude-bestpractice"

# The one array this module writes. `allow`, `soft_deny` and `hard_deny` are grants and
# refusals — discretionary by nature, and the founder's.
ENVIRONMENT = "environment"


def marker(ctx: GitContext) -> str:
    return f"{MARK} {ctx.worktree_root.as_posix()}]"


def settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / SETTINGS


def read(home: Path | None = None) -> dict:
    """The founder's settings, or an empty mapping. Never raises: this is read on a hook."""
    try:
        raw = json.loads(settings_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _entries(settings: dict, key: str) -> list[str]:
    block = (settings.get("autoMode") or {}).get(key)
    return [str(item) for item in block] if isinstance(block, list) else []


def facts(ctx: GitContext, test_command: list[str]) -> list[str]:
    """What this repository IS, in the sentences the classifier reads.

    Derived on every call rather than remembered, so a claim cannot outlive its subject —
    which is the failure that put a revoked production key in a live environment rule for
    a day after it had been moved and revoked.
    """
    from . import gitpolicy

    mark = marker(ctx)
    root = ctx.worktree_root.as_posix()
    trunk = gitpolicy.default_branch(ctx) or "main"
    checks = " ".join(test_command) if test_command else "not detected"
    remotes = ", ".join(_remotes(ctx)) or "none"
    return [
        f"{mark} {root} is a git repository governed by the claude-bestpractice plugin. "
        f"Its trunk is {trunk} and its remotes are: {remotes}.",
        f"{mark} This project's checks are `{checks}`. The plugin's Stop gate runs them "
        "itself and refuses a finish without a passing run, so running them is expected "
        "work rather than something to ask about.",
        f"{mark} Several Claude sessions work in this repository at once, each in its own "
        f"worktree under {root}/.claude/worktrees/. They share one clone, so they share "
        "one git object store and one set of branches.",
        f"{mark} The plugin's PreToolUse gate judges every call in this repository before "
        "the classifier sees it, and approves the ones it ordered — reads, this project's "
        "checks, writes in the tree the session owns, worktree moves, and a pull request "
        "it has just found no blockers for. Anything it stays silent on is deliberate.",
    ]


def _remotes(ctx: GitContext) -> list[str]:
    from .gitctx import _run

    try:
        names = _run(["remote"], ctx.worktree_root, check=False).split()
    except (OSError, ValueError):
        return []
    out = []
    for name in names[:8]:
        try:
            url = _run(["remote", "get-url", name], ctx.worktree_root, check=False).strip()
        except (OSError, ValueError):
            continue
        if url:
            out.append(f"{name} -> {url}")
    return out


@dataclass
class Delta:
    """What applying would change, computed before anything is written."""

    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.add and not self.remove


def delta(ctx: GitContext, test_command: list[str], home: Path | None = None) -> Delta:
    settings = read(home)
    mark = marker(ctx)
    current = [line for line in _entries(settings, ENVIRONMENT) if line.startswith(mark)]
    wanted = facts(ctx, test_command)
    return Delta(
        add=[line for line in wanted if line not in current],
        remove=[line for line in current if line not in wanted],
        dead=dead_rules(settings),
    )


# Prefix rules the vouch now answers by predicate, so the hand-written entry does nothing.
# Reported and never removed: a rule that is redundant HERE may be the reason something
# works in another repository on the same machine, and this module cannot see that one.
_ANSWERED_BY_VOUCH = (
    "Bash(git worktree", "Bash(git log", "Bash(git diff", "Bash(git status",
    "Bash(git show", "Bash(git rev-parse", "Bash(cat", "Bash(grep", "Bash(rg",
)

# Tool-name entries the CLI's own safety check outranks, so the line reads as a permission
# that was granted and is not being honoured (#111).
_OUTRANKED = ("EnterWorktree", "ExitWorktree")


def dead_rules(settings: dict) -> list[str]:
    """Hand-written rules that no longer do anything, named so they can be deleted.

    A hand-maintained list has no expiry and no test. Eight worktree entries on one machine,
    six of them inert because the vouch answers `git worktree` by predicate and two of them
    unable to work at all — with nothing anywhere telling the founder which was which.
    """
    allow = (settings.get("permissions") or {}).get("allow")
    listed = [str(rule) for rule in allow] if isinstance(allow, list) else []
    out: list[str] = []
    for rule in listed:
        if rule.startswith(_ANSWERED_BY_VOUCH):
            out.append(f"{rule} — the gate already answers this by predicate")
        elif rule.split("(")[0].strip() in _OUTRANKED:
            out.append(f"{rule} — the CLI's own safety check outranks this; it never fires")
    return out


def apply(ctx: GitContext, test_command: list[str], home: Path | None = None) -> Delta:
    """Write this repository's generated facts, and nothing else. Returns what changed.

    Reads, rewrites and removes only entries carrying this repository's marker. Everything
    else in the file — the founder's prose, another repository's block, keys this module
    has never heard of — is carried through untouched.
    """
    found = delta(ctx, test_command, home)
    if found.in_sync:
        return found

    settings = read(home)
    mark = marker(ctx)
    auto = settings.get("autoMode")
    if not isinstance(auto, dict):
        auto = {}
    kept = [line for line in _entries(settings, ENVIRONMENT) if not line.startswith(mark)]
    auto[ENVIRONMENT] = kept + facts(ctx, test_command)
    settings["autoMode"] = auto

    path = settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.atomic_write(path, json.dumps(settings, indent=2, ensure_ascii=False), mode=0o600)
    return found


def line(ctx: GitContext, test_command: list[str], home: Path | None = None) -> str:
    """The board's one line, and only when there is something to do. Usually empty.

    Named as a command the AGENT runs. The whole complaint behind this is that the founder
    was the integration layer between two policy layers, and a line telling them to go and
    edit a file would be that complaint restated.
    """
    found = delta(ctx, test_command, home)
    if found.in_sync and not found.dead:
        return ""
    parts = []
    if not found.in_sync:
        parts.append(f"{len(found.add)} to write, {len(found.remove)} stale")
    if found.dead:
        parts.append(f"{len(found.dead)} hand-written rule(s) now dead")
    return (
        f"\nauto-mode policy: {'; '.join(parts)}. Run `claude-bp policy --apply` yourself — "
        "it writes only facts this repository proves, and never touches a rule the founder "
        "wrote."
    )
