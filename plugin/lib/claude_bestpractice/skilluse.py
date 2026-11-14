"""How often each skill a session can load is read, and which used ones can no longer be.

A skill is chosen by the model: its description rides in every request, and its body loads only
when Claude picks it. Ponytail's skill, tested alone, "self-activated zero times" across ten
sessions (JetBrains, July 2026), and a description no request reaches is always-on context
bought for nothing (decision 0080).

Counted for every skill, not only this plugin's (#364): a founder's two most important domain
skills had no Skill-tool call in a month, because a rule file sent sessions to read their
`SKILL.md` directly, 17 and 2 times, and nothing counted that either. So two counts: an
expansion, which the hooks module reports from `skill.prompt` (the Skill tool, a slash command,
a preload), and a read of the file itself, which it reports from `Read`. The count starts at a
session start, so a skill never read shows as read none.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from . import PLUGIN, skills, store
from .gitctx import GitContext

FILE = "skill-uses.json"

# What Claude Code calls a skill synced from claude.ai in `skill.prompt`'s `source`.
_SYNCED_SOURCE = "syncedSkills"


def ours() -> list[str]:
    """The skills this plugin ships, by the name each is listed under."""
    return sorted(skill.path.parent.name for skill in skills.ours())


def counting(ctx: GitContext) -> None:
    """Start the count on this clone, once."""
    path = store.tier_b(ctx, FILE)
    if path.exists():
        return
    with store.guarded_json(path, default={}) as box:
        if not isinstance(box[0], dict) or "since" not in box[0]:
            box[0] = {"since": time.time(), "uses": {}}


def qualified(skill: str, plugin: str = "", source: str = "") -> str:
    """The name a skill is counted under: as a session invokes it, and this plugin's own bare,
    as decision 0080 counted them (`llm-first-code`, `deploy`, `anthropic-skills:pdf`,
    `other-plugin:review`)."""
    bare = skill.rsplit(":", 1)[-1]
    if (plugin == PLUGIN or skill.startswith(f"{PLUGIN}:")) and bare in ours():
        return bare
    if ":" in skill:
        return skill
    if source == _SYNCED_SOURCE:
        return f"{skills.SYNCED_NAMESPACE}:{skill}"
    return f"{plugin}:{skill}" if plugin else skill


def used(ctx: GitContext, skill: str, plugin: str = "", source: str = "", path: str = "") -> None:
    """Count one expansion of `skill`, from `plugin` where a plugin ships it, loaded from `path`."""
    if skill:
        _count(ctx, "uses", qualified(skill, plugin, source), path)


def read_directly(ctx: GitContext, path: str) -> None:
    """Count one read of a skill's or a command's file, by the skill it is."""
    name = _named_by(ctx, Path(path)) if path else ""
    if name:
        _count(ctx, "reads", name, path)


def _named_by(ctx: GitContext, path: Path) -> str:
    """The skill a file is: the listed one at that path, else this plugin's by its folder, else
    the folder's name for a `SKILL.md` and the stem for a command."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for listed in skills.inventory(ctx.worktree_root):
        if listed.path == path or listed.path.resolve() == resolved:
            return listed.name
    if resolved.parent.parent == skills.SHIPPED.resolve():
        return resolved.parent.name
    return path.parent.name if path.name == "SKILL.md" else path.stem


def _count(ctx: GitContext, kind: str, name: str, path: str) -> None:
    with store.guarded_json(store.tier_b(ctx, FILE), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        table.setdefault("since", time.time())
        counts = table.setdefault(kind, {})
        counts[name] = int(counts.get(name) or 0) + 1
        if path:
            table.setdefault("from", {})[name] = path
        box[0] = table


def counts(ctx: GitContext) -> dict[str, tuple[int, int]]:
    """Each counted skill's (expansions, reads of its file)."""
    table = _table(ctx)
    uses, reads = _numbers(table, "uses"), _numbers(table, "reads")
    return {name: (uses.get(name, 0), reads.get(name, 0)) for name in {*uses, *reads}}


def since(ctx: GitContext) -> str:
    """The day the count began, `YYYY-MM-DD`, or "" before it did."""
    began = _table(ctx).get("since")
    return time.strftime("%Y-%m-%d", time.localtime(began)) if isinstance(began, (int, float)) else ""


def line(ctx: GitContext) -> str:
    """`skills read: <name> <count>, … since <date>`, for this plugin's own, or "" before any
    count began. A read of the file is added as `+<n> by file`."""
    began = since(ctx)
    if not began:
        return ""
    seen = counts(ctx)
    listed = ", ".join(f"{name} {seen.get(name, (0, 0))[0]}" + _by_file(seen.get(name, (0, 0))[1])
                       for name in ours())
    return f"skills read: {listed}, since {began}" if listed else ""


def _by_file(reads: int) -> str:
    return f" (+{reads} by file)" if reads else ""


def vanished(ctx: GitContext, listed: list[skills.Listed]) -> list[tuple[str, str]]:
    """Each skill read since the count began that no source lists any longer, with the file it
    was last loaded from: (name, path)."""
    loadable = {item.name for item in listed} | set(ours())
    table = _table(ctx)
    origins = table.get("from") if isinstance(table.get("from"), dict) else {}
    return sorted((name, str(origins.get(name) or "")) for name in counts(ctx)
                  if name not in loadable)


def _table(ctx: GitContext) -> dict:
    table = store.read_json(store.tier_b(ctx, FILE), default={})
    return table if isinstance(table, dict) else {}


def _numbers(table: dict, kind: str) -> dict[str, int]:
    found = table.get(kind)
    return {str(name): int(count) for name, count in found.items()
            if isinstance(count, int)} if isinstance(found, dict) else {}


NOTICES = "skill-notices.json"

# How many counted skills `report` names on its read line, most read first; ours always.
_NAMED = 10

_KINDS = (skills.PROJECT, skills.USER, skills.SYNCED, skills.PLUGIN)


def report(ctx: GitContext, home: Path | None = None) -> list[str]:
    """`claude-bp status`'s SKILLS lines: what a session here can load, by source; what was read,
    by expansion and by file; what was read and can no longer be loaded; the skills of a
    claude.ai account this machine stopped syncing; and what is due for a fresh look."""
    listed = skills.inventory(ctx.worktree_root, home)
    lines = [_loadable_line(listed), _read_line(ctx), _unread_line(ctx, listed)]
    lines += [f"gone: {name}, last loaded from {where or 'a file not recorded'}"
              for name, where in vanished(ctx, listed)]
    lines += [f"left behind: {', '.join(account.names)}, synced from a claude.ai account until "
              f"{_day(account.synced_at)}, load in no session now ({account.folder})"
              for account in skills.left_behind(home)]
    return [line for line in [*lines, skills.overdue_line()] if line]


def _loadable_line(listed: list[skills.Listed]) -> str:
    counted = Counter(item.kind for item in listed)
    by_kind = ", ".join(f"{counted[kind]} {kind}" for kind in _KINDS if counted[kind])
    return f"{len(listed)} loadable" + (f": {by_kind}" if by_kind else "")


def _read_line(ctx: GitContext) -> str:
    began = since(ctx)
    if not began:
        return ""
    seen = counts(ctx)
    named = sorted(seen, key=lambda name: (-sum(seen[name]), name))[:_NAMED]
    named += [name for name in ours() if name not in named]
    shown = ", ".join(f"{name} {seen.get(name, (0, 0))[0]}" + _by_file(seen.get(name, (0, 0))[1])
                      for name in named)
    return f"read since {began}: {shown}"


def _unread_line(ctx: GitContext, listed: list[skills.Listed]) -> str:
    """The repository's and the founder's own skills nobody read since the count began. A
    synced or plugin skill nobody read is somebody else's to keep, and their lists are long."""
    if not since(ctx):
        return ""
    seen = counts(ctx)
    unread = sorted(item.name for item in listed
                    if item.kind in (skills.PROJECT, skills.USER) and item.name not in seen)
    return f"never read: {', '.join(unread)}" if unread else ""


def _day(stamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(stamp)) if stamp else "an unknown day"


def notice(ctx: GitContext, home: Path | None = None) -> str:
    """One line for the session-start board, once per skill: a skill read here that no source
    lists any longer, and the skills of a claude.ai account this machine stopped syncing. ""
    when there is nothing new, which is every start but the first after it happened.

    Three synced skills, one the second most used in the repository, went with an account
    change and no session noticed for four weeks (#364).
    """
    found = {f"gone:{name}": f"{name} (last loaded from {where or 'a file not recorded'})"
             for name, where in vanished(ctx, skills.inventory(ctx.worktree_root, home))}
    found.update({f"behind:{name}": f"{name} (synced from a claude.ai account until "
                                    f"{_day(account.synced_at)})"
                  for account in skills.left_behind(home) for name in account.names})
    said = _not_told(ctx, found)
    if not said:
        return ""
    return (f"\nskills no session here can load any longer: {', '.join(said)}. A rule or a skill "
            "that names one sends the session to nothing; `claude-bp status` lists what loads.")


def _not_told(ctx: GitContext, found: dict[str, str]) -> list[str]:
    """The texts of `found` the board has not said yet, by key, each marked said."""
    with store.guarded_json(store.tier_b(ctx, NOTICES), default={}) as box:
        told = box[0] if isinstance(box[0], dict) else {}
        fresh = [key for key in found if key not in told]
        told.update({key: time.time() for key in fresh})
        box[0] = told
    return [found[key] for key in fresh]
