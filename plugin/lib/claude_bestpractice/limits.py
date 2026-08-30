"""What the account has left, carried from where it is shown to where it is decided.

Claude Code knows the five-hour and weekly usage of the account it is running under, and
hands both to exactly one place: the `statusLine` command, on stdin, as
`rate_limits.five_hour` and `rate_limits.seven_day`. Hooks do not receive it, there is no
`claude usage` subcommand, and nothing on disk holds it — checked all four before building
this.

That one place shows the numbers to the FOUNDER. The model, which is the thing deciding
whether to start a two-hour rehearsal or a forty-agent sweep, cannot see them at all. So
this module is a bridge: the status line records what it was given, and the board reads it
back at session start.

Read-only about the account. Nothing here throttles anything — a plugin that decided on the
founder's behalf when to stop working would be making a spending decision that is theirs.
It reports, and a session that knows it has 4% of the week left can say so before starting
something long instead of dying in the middle of it.
"""

from __future__ import annotations

import time

from . import store
from .gitctx import GitContext

FILE = "rate-limits.json"

# Older than this and the numbers are not worth printing: usage moves, and a stale
# percentage is worse than none because it reads as current. Six hours also outlives the
# five-hour window itself, so nothing carries across a reset.
MAX_AGE_SECONDS = 6 * 3600


def record(ctx: GitContext, payload: dict) -> dict:
    """Keep what the status line was handed, if it was handed anything."""
    limits = (payload or {}).get("rate_limits")
    if not isinstance(limits, dict):
        return {}
    kept = {"at": int(time.time())}
    for window in ("five_hour", "seven_day"):
        found = limits.get(window)
        if not isinstance(found, dict):
            continue
        used = found.get("used_percentage")
        resets = found.get("resets_at")
        if used is None and resets is None:
            continue
        kept[window] = {"used_percentage": used, "resets_at": resets}
    if len(kept) == 1:
        return {}
    store.write_json(store.tier_b(ctx, FILE), kept)
    return kept


def load(ctx: GitContext) -> dict:
    found = store.read_json(store.tier_b(ctx, FILE), default={})
    return found if isinstance(found, dict) else {}


def _in(seconds: float) -> str:
    """`2h11m`, `3d4h`, or `now` — short enough for a line nobody asked for."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86_400}d{(seconds % 86_400) // 3600}h"


def _window(name: str, body: dict, now: float) -> str:
    used = body.get("used_percentage")
    resets = body.get("resets_at")
    if used is None:
        return ""
    shown = f"{name} {float(used):.0f}%"
    if isinstance(resets, (int, float)) and resets > now:
        shown += f" (resets in {_in(float(resets) - now)})"
    return shown


def line(ctx: GitContext, now: float | None = None) -> str:
    """One line for the board, or nothing. Silent until a status line has run once.

    Deliberately not a warning. What counts as "too little left" depends on what the
    founder is about to spend it on, which this cannot know — so it states the number and
    leaves the judgement where it belongs.
    """
    now = time.time() if now is None else now
    kept = load(ctx)
    stamped = kept.get("at")
    if not isinstance(stamped, (int, float)) or now - stamped > MAX_AGE_SECONDS:
        return ""
    parts = [p for p in (_window("5h", kept.get("five_hour") or {}, now),
                         _window("week", kept.get("seven_day") or {}, now)) if p]
    if not parts:
        return ""
    # Where the LIVE number is, because this one ages. The status line rewrites that file
    # continuously while the session runs, but the board is injected once at session start
    # — decision 0003, and re-injecting per turn costs O(T^2) against O(T). So an
    # eleven-hour session is holding an eleven-hour-old percentage unless it goes and looks,
    # and the only thing that makes looking possible is knowing where.
    return ("\nlimits: " + " · ".join(parts)
            + f" (as at session start; live: {store.tier_b(ctx, FILE)})")


# `~/.claude/settings.json`, the only place a status line can be configured.
SETTING = "statusLine"


def installed(home=None) -> str:
    """The status line configured today, or "" when there is none."""
    from . import policy

    found = (policy.read(home) or {}).get(SETTING)
    if isinstance(found, dict):
        return str(found.get("command") or "")
    return str(found or "")


def install(command: str, home=None) -> tuple:
    """Wire the plugin's status line in, but never over one that is already there.

    A status line is the founder's own display, not state this plugin owns, so the rule is
    the opposite of `policy`: nothing is written when the key is taken. Decision 0008 lets
    this plugin write FACTS about the repository into that file; somebody's status bar is
    neither a fact nor a grant, and taking it over unasked is how a tool gets uninstalled.
    """
    import json

    from . import policy, store

    current = installed(home)
    if current and "claude-bp-statusline" not in current:
        return False, current
    if current:
        return True, current

    settings = policy.read(home)
    settings[SETTING] = {"type": "command", "command": command}
    path = policy.settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.atomic_write(path, json.dumps(settings, indent=2, ensure_ascii=False),
                       mode=0o600, follow_symlink=True)
    return True, command
