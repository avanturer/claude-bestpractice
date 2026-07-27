"""Detect and neutralise everything that competes for the same job.

Contradictory instruction is the failure this plugin exists to prevent, and the harness
resolves a contradiction by picking one arbitrarily. All discovered instruction files
are CONCATENATED rather than overriding each other, so a stale block written into a
global file by some tool a year ago silently argues with us in every project on the
machine.

What can actually be done, and what cannot:

* A plugin CANNOT disable another plugin. No manifest field exists — verified by
  grepping the installed binary for every plausible key. Dependencies point one way:
  require, never exclude. So governance is CURATION plus HOOKS, not veto.
* Loose hooks written directly into project settings by other installers are not
  plugins, are invisible to plugin listings, and will double-fire alongside ours.
  Those we can quarantine, because they live in a file we may edit.
* Competing instruction files can be excluded by path.
* Everything else is reported, never silently altered. Deleting another tool's
  configuration without asking is how a plugin gets uninstalled in anger.

Nothing here modifies anything unless explicitly asked. Detection runs anywhere;
`apply` is a deliberate command.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import store
from .gitctx import GitContext

QUARANTINE_KEY = "_founderOsQuarantined"
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")

# Tools that own the same territory. Presence is not a problem by itself; two systems
# both injecting project context, both gating Stop, or both writing memory is.
_COMPETING_MARKERS: list[tuple[str, str, str]] = [
    ("claude-mem", "memory", "injects its own session context and writes a parallel memory store"),
    ("superpowers", "workflow", "injects an unconditional instruction block every session"),
    ("hookify", "enforcement", "runs its own rule engine on the same events"),
    ("ruflo", "workflow", "appends a block to the global instruction file"),
    ("superclaude", "workflow", "installs a large always-on command and agent set"),
    ("claude-flow", "orchestration", "runs a competing session orchestrator"),
]

# Events where two independent handlers produce two independent outcomes for one action.
_CONTESTED_EVENTS = {"SessionStart", "Stop", "PreToolUse", "PreCompact", "SubagentStart"}


@dataclass
class Conflict:
    kind: str
    where: str
    detail: str
    action: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ours(command: str) -> bool:
    return "claude-bestpractice" in command or "claude_bestpractice" in command


def _handlers(matchers: object) -> list[dict]:
    """Every handler under an event, tolerating any shape another tool wrote."""
    out: list[dict] = []
    if not isinstance(matchers, list):
        return out
    for matcher in matchers:
        if isinstance(matcher, dict):
            out.extend(h for h in matcher.get("hooks", []) if isinstance(h, dict))
    return out


def loose_hooks(ctx: GitContext) -> list[Conflict]:
    """Hook entries written straight into project settings by another installer.

    These are the ones that actually double-fire: two Stop gates disagreeing about
    whether the turn may end, two PreToolUse handlers both returning a decision with no
    documented precedence between them.
    """
    out: list[Conflict] = []
    for rel in SETTINGS_FILES:
        hooks = _read_json(ctx.worktree_root / rel).get("hooks")
        if not isinstance(hooks, dict):
            continue
        for event, matchers in hooks.items():
            if event == QUARANTINE_KEY:
                continue
            contested = event in _CONTESTED_EVENTS
            for handler in _handlers(matchers):
                command = str(handler.get("command", ""))
                if _ours(command):
                    continue
                out.append(
                    Conflict(
                        "loose-hook",
                        f"{rel}:{event}",
                        f"{command[:80]} — "
                        + ("contests an event we own" if contested else "runs alongside ours"),
                        "quarantine" if contested else "report",
                    )
                )
    return out


def competing_instructions(ctx: GitContext) -> list[Conflict]:
    """Instruction files that will be concatenated with ours and may contradict them."""
    out: list[Conflict] = []
    candidates = [
        Path.home() / ".claude" / "CLAUDE.md",
        ctx.worktree_root / "AGENTS.md",
        ctx.worktree_root / ".cursorrules",
        ctx.worktree_root / ".windsurfrules",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 200:
            continue
        out.append(
            Conflict(
                "instruction-file",
                str(path),
                f"{size} bytes concatenated into every session; contradictions resolve arbitrarily",
                "exclude",
            )
        )
    return out


def _installed_plugin_names() -> set[str]:
    """Plugin identifiers from both places the harness records them."""
    names: list[str] = []
    installed = _read_json(Path.home() / ".claude" / "plugins" / "installed_plugins.json")
    for key, value in installed.items():
        names.append(str(key))
        if isinstance(value, dict):
            names.extend(str(k) for k in value)

    enabled = _read_json(Path.home() / ".claude" / "settings.json").get("enabledPlugins")
    if isinstance(enabled, dict):
        names.extend(str(k) for k in enabled)
    return {name.lower() for name in names}


def competing_plugins() -> list[Conflict]:
    """Installed plugins that own the same job. Reported; never touched automatically."""
    installed = _installed_plugin_names()
    return [
        Conflict("competing-plugin", marker, f"owns {territory}: {why}", "disable")
        for marker, territory, why in _COMPETING_MARKERS
        if any(marker in name for name in installed)
    ]


def detect(ctx: GitContext) -> list[Conflict]:
    return loose_hooks(ctx) + competing_instructions(ctx) + competing_plugins()


def quarantine_loose_hooks(ctx: GitContext) -> tuple[int, list[str]]:
    """Move contesting hook entries into a labelled block instead of deleting them.

    Never a silent delete. The founder's own hooks may be load-bearing for something
    unrelated, and a plugin that removes another tool's configuration without saying so
    is a plugin that gets uninstalled the first time someone notices.

    Returns (moved, backups written).
    """
    moved = 0
    backups: list[str] = []

    for rel in SETTINGS_FILES:
        path = ctx.worktree_root / rel
        data = _read_json(path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            continue

        quarantined = data.get(QUARANTINE_KEY)
        quarantined = quarantined if isinstance(quarantined, dict) else {}
        remaining: dict[str, list] = {}

        for event, matchers in hooks.items():
            if event == QUARANTINE_KEY or not isinstance(matchers, list):
                continue
            keep, park = _split_matchers(event, matchers)
            if keep:
                remaining[event] = keep
            if park:
                quarantined.setdefault(event, []).extend(park)
                moved += len(park)

        if not moved:
            continue

        # 0600, not 0644. settings.local.json is where personal tokens live, and the
        # backup is a NEW file no standard .gitignore covers — so a world-readable copy
        # of someone's credentials appears next to it, ready for the next `git add -A`.
        backup = path.with_suffix(path.suffix + ".claude-bestpractice.bak")
        store.atomic_write(backup, json.dumps(data, indent=2), mode=0o600)
        backups.append(backup.name)

        data["hooks"] = remaining
        data[QUARANTINE_KEY] = quarantined
        store.atomic_write(path, json.dumps(data, indent=2) + "\n", mode=0o644)

    return moved, backups


def _split_matchers(event: str, matchers: list) -> tuple[list, list]:
    """Ours stays, theirs is parked — but only on events where two answers conflict.

    An uncontested event (a formatter on PostToolUse, say) is left entirely alone. This
    is a targeted takeover of the decisions we own, not a land grab.
    """
    if event not in _CONTESTED_EVENTS:
        return list(matchers), []

    keep, park = [], []
    for matcher in matchers:
        handlers = _handlers([matcher])
        if handlers and all(_ours(str(h.get("command", ""))) for h in handlers):
            keep.append(matcher)
        else:
            park.append(matcher)
    return keep, park


def restore_quarantined(ctx: GitContext) -> int:
    """Put quarantined hooks back. An override nobody can undo is a trap."""
    restored = 0
    for rel in SETTINGS_FILES:
        path = ctx.worktree_root / rel
        data = _read_json(path)
        parked = data.get(QUARANTINE_KEY)
        if not isinstance(parked, dict) or not parked:
            continue
        hooks = data.get("hooks")
        hooks = hooks if isinstance(hooks, dict) else {}
        for event, matchers in parked.items():
            hooks.setdefault(event, []).extend(matchers)
            restored += len(matchers)
        data["hooks"] = hooks
        data.pop(QUARANTINE_KEY, None)
        store.atomic_write(path, json.dumps(data, indent=2) + "\n", mode=0o644)
    return restored


def exclusion_settings(conflicts: list[Conflict]) -> dict:
    """The settings block that stops competing instruction files being concatenated."""
    excludes = [c.where for c in conflicts if c.kind == "instruction-file"]
    return {"claudeMdExcludes": excludes} if excludes else {}


_ANSI = re.compile(r"\033\[[0-9;]*m")


def render(conflicts: list[Conflict]) -> str:
    if not conflicts:
        return "no conflicts detected"
    lines = [f"{len(conflicts)} conflict(s):"]
    for conflict in conflicts:
        lines.append(f"  {conflict}")
        lines.append(f"      -> {conflict.action}")
    return _ANSI.sub("", "\n".join(lines))
