"""How often this plugin's skills are read, counted where Claude Code expands one.

A skill is chosen by the model: its description rides in every request, and its body loads only
when Claude picks it. Ponytail's skill, tested alone, "self-activated zero times" across ten
sessions (JetBrains, July 2026), and a description no request reaches is always-on context
bought for nothing. The hooks module tells this module each time one of this plugin's skills is
expanded; `claude-bp status` says what each came to, and since when (decision 0078). The count
starts at a session start, so a skill never read shows as read none.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import store
from .gitctx import GitContext

FILE = "skill-uses.json"
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


def ours() -> list[str]:
    """The skills this plugin ships, by the name each is listed under."""
    try:
        return sorted(path.name for path in SKILLS_DIR.iterdir() if (path / "SKILL.md").is_file())
    except OSError:
        return []


def counting(ctx: GitContext) -> None:
    """Start the count on this clone, once."""
    path = store.tier_b(ctx, FILE)
    if path.exists():
        return
    with store.guarded_json(path, default={}) as box:
        if not isinstance(box[0], dict) or "since" not in box[0]:
            box[0] = {"since": time.time(), "uses": {}}


def used(ctx: GitContext, skill: str) -> None:
    """Count one expansion of `skill`, named bare or as `plugin:skill`, where it is one of ours."""
    name = skill.rsplit(":", 1)[-1]
    if name not in ours():
        return
    with store.guarded_json(store.tier_b(ctx, FILE), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        table.setdefault("since", time.time())
        uses = table.setdefault("uses", {})
        uses[name] = int(uses.get(name) or 0) + 1
        box[0] = table


def line(ctx: GitContext) -> str:
    """`skills read: <name> <count>, … since <date>`, or "" before any count began."""
    table = store.read_json(store.tier_b(ctx, FILE), default={})
    if not isinstance(table, dict) or not isinstance(table.get("since"), (int, float)):
        return ""
    uses = table.get("uses") if isinstance(table.get("uses"), dict) else {}
    since = time.strftime("%Y-%m-%d", time.localtime(table["since"]))
    counted = ", ".join(f"{name} {int(uses.get(name) or 0)}" for name in ours())
    return f"skills read: {counted}, since {since}" if counted else ""
