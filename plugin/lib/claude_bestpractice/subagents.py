"""Who a subagent runs as, and how many of them a turn is allowed to start.

A spawn is the one tool call whose cost is decided entirely by a field nobody has to
fill in. Claude Code resolves the model in this order — the call's own `model`, the
agent definition's frontmatter, `CLAUDE_CODE_SUBAGENT_MODEL`, and then the session's
model — so a call that names nothing runs on whatever the parent is running on, which
in this founder's sessions is the most expensive model available. Three Explore agents
grepping for a filename then cost three times what the turn is worth, and nothing
anywhere records that a choice was made, because none was.

Two things are asked here, and only the two that a gate can actually decide.

THE TIER IS NAMED. Either in the call or pinned in the agent's own definition, which is
the founder's file and their choice to make once. `inherit` counts from a definition and
not from a call: in a file it is a decision somebody wrote down, in a call it is the
default spelled out. A fork names nothing and is not asked to: it runs on the session's
own model whatever the call says (`FORK`).

THE COUNT IS BOUNDED. Parallel agents are the cheapest-looking way to go faster and the
most expensive way to be wrong: each one pays for its own context, and four searching
the same repository produce four copies of the same answer. `subagent_fanout` is the
founder's number, and past it the spawn is refused with what it costs.

What is deliberately NOT asked: a written justification. No gate can tell a true reason
from a plausible one, and demanding a sentence would buy a sentence. The tier guide goes
in the refusal and in the skill, where a reason belongs.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import store
from .gitctx import GitContext

# Both spellings the harness has used for the spawn. The tool is `Agent` in current
# Claude Code tool events; `Task` is what it was called, and a matcher that lost the old
# name on an older install would be a gate that silently stops running.
SPAWN_TOOLS = ("Agent", "Task")

# The aliases Claude Code accepts, in the order they cost. Verified against the model
# configuration documentation rather than remembered: a tier list this plugin invented
# would refuse a spelling the harness accepts, which is a gate that blocks correct work.
TIERS = ("haiku", "sonnet", "opus", "fable")
INHERIT = "inherit"

# The spawn that copies this session instead of starting an agent. Claude Code runs it on the
# parent's model and ignores `model` on the call, because the fork reads the parent's prompt
# cache and another model would throw that cache away. So nobody defaulted into anything, and
# the tier refusal asked a fork for a field the harness discards: one more round trip on every
# fork, which 2.1.302 turned on by default in VS Code, the desktop app and `claude -p`.
FORK = "fork"


def is_a_fork(subagent_type: str) -> bool:
    """Is this spawn a fork of the session, which runs on its model whatever the call names?"""
    return (subagent_type or "").strip().lower() == FORK

# A pinned full model id is a named tier too — `claude-sonnet-5`, `claude-opus-5`. The
# point is that somebody chose, not that they chose from a list of four.
_MODEL_ID = re.compile(r"^claude-[a-z0-9.\-]+$", re.I)

_FRONTMATTER_MODEL = re.compile(r"^model\s*:\s*(?P<model>[^\s#]+)", re.M)

WHAT_EACH_TIER_IS_FOR = (
    "    haiku   runs as `subagent_min_model` when that is higher, sonnet unless the founder "
    "lowered it\n"
    "    sonnet  ordinary implementation and review — most delegated work belongs here\n"
    "    opus    design, ambiguity, a judgement the parent cannot make for itself\n"
    "    fable   a long autonomous run that has to investigate before it acts\n"
)


def names_a_tier(value: str) -> bool:
    """Is this a model the caller actually chose, rather than one they defaulted into?"""
    named = (value or "").strip().lower()
    return named in TIERS or bool(_MODEL_ID.match(named))


def pinned_tier(ctx: GitContext, subagent_type: str, home: Path | None = None) -> str:
    """The model an agent's own definition pins, or "" when it pins none.

    Three places, which is where a definition can be: this repository, the founder's home
    directory, and the plugin the call came from. A plugin-scoped `plugin:agent` name is
    looked up by its last segment, because that is the filename.

    The built-in agents — Explore, Plan, general-purpose — have no file anywhere, so they
    pin nothing and every call that uses one has to say what it runs as. That is not an
    oversight in this rule, it is the case it exists for: those three are what a session
    reaches for without thinking.
    """
    name = (subagent_type or "").strip().split(":")[-1]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""
    for directory in _agent_dirs(ctx, home):
        found = directory / f"{name}.md"
        try:
            text = found.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _FRONTMATTER_MODEL.search(text.split("---", 2)[1] if "---" in text else text)
        if match:
            return match.group("model").strip().strip("\"'").lower()
    return ""


def _agent_dirs(ctx: GitContext, home: Path | None = None) -> list[Path]:
    """Where an agent definition can live, nearest first."""
    dirs = [ctx.worktree_root / ".claude" / "agents", (home or Path.home()) / ".claude" / "agents"]
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        dirs.append(Path(plugin_root) / "agents")
    return dirs


def tier_refusal(subagent_type: str, pinned: str) -> str:
    """Why a spawn that names no tier is refused, and how to name one."""
    named = subagent_type or "this agent"
    pinned_said = (
        f"  Its definition says `model: {pinned}`, which is the parent's model spelled "
        "out rather than a tier chosen for the work.\n" if pinned == INHERIT else
        f"  Nothing pins a model for `{named}`, so it would run on this session's own "
        "model — the most expensive one in the room.\n"
    )
    return (
        f"claude-bestpractice: this spawn of `{named}` does not say what model it runs as.\n"
        f"{pinned_said}"
        "  Pass `model` on the call:\n"
        f"{WHAT_EACH_TIER_IS_FOR}"
        "  A tier pinned in the agent's own definition counts and needs nothing here — "
        "that is the founder's choice, made once."
    )


def fanout_refusal(spawned: int, ceiling: int) -> str:
    """Why the next parallel agent is refused, in what it costs rather than in a rule."""
    return (
        f"claude-bestpractice: this turn has already started {spawned} subagent(s), and "
        f"the ceiling is {ceiling}.\n"
        "  Each one pays for its own context of this repository before it reads a single "
        "line of the answer, so a fan-out is the most expensive way there is to go "
        "slightly faster — and agents searching the same tree return the same answer "
        f"{spawned} times.\n"
        "  Use what the ones already running come back with, or do this part yourself. "
        "If the work genuinely needs more, the founder raises `subagent_fanout`; it is "
        "their number, not this session's."
    )


# What Stop's `background_tasks` says of work still under way. A missing or unknown status is no
# work at all: a record the harness never ends must not stand for work being done.
UNDER_WAY = frozenset({"running", "pending", "in_progress", "starting", "queued"})


# ---------------------------------------------------------------------------------------------
# The agents a chat stopped, kept for the card it pauses (decision 0082).

# The kinds a SendMessage to the id resumes with its own context: a subagent, as Stop's
# `background_tasks` labels one and as TaskStop's result names it (`local_agent`, measured on
# Claude Code 2.1.319). A workflow has a pause of its own, in `/workflows`, and a teammate is a
# session of its own, whose idle entry the harness keeps `running` (anthropics/claude-code#85955).
RESUMABLE = frozenset({"subagent", "local_agent"})
STOP_TOOL = "TaskStop"
ROSTER_DIR = "stopped-agents"
ROSTER_LIMIT = 40
DESCRIPTION_CHARS = 120


def running(tasks: object) -> list[dict]:
    """The agents a Stop's `background_tasks` lists as still at work, of a kind that resumes."""
    out: list[dict] = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict) or not str(task.get("id") or "").strip():
            continue
        kind = str(task.get("type") or "").strip().lower()
        if kind in RESUMABLE and str(task.get("status") or "").strip().lower() in UNDER_WAY:
            out.append(task)
    return out


def _roster(ctx: GitContext, chat: str) -> Path:
    return store.tier_b(ctx, ROSTER_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", chat)[:120] + ".json")


def _when(row: object) -> float:
    """When a kept agent was stopped, 0.0 where the row cannot say."""
    try:
        return float(row.get("stopped") or 0) if isinstance(row, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def stopped(ctx: GitContext, chat: str, response: object) -> str:
    """Keep the agent one of the chat's `TaskStop` calls stopped, by what it was sent to do. The
    id kept, or "" for a stop of anything a message does not resume, a shell or a monitor.

    Read off the call's result, which names the task and its kind, `{"task_id": …, "task_type":
    "local_agent", "command": <its description>}` on Claude Code 2.1.319: the input names an id
    alone, and a shell's id looks like an agent's.
    """
    from . import redact

    found = response if isinstance(response, dict) else {}
    ident = str(found.get("task_id") or "").strip()
    if not chat or not ident or str(found.get("task_type") or "").strip().lower() not in RESUMABLE:
        return ""
    said = redact.scrub(" ".join(redact.strip_control(str(found.get("command") or "")).split()))
    with store.guarded_json(_roster(ctx, chat), default={}) as box:
        roster = box[0] if isinstance(box[0], dict) else {}
        roster[ident] = {"description": said[:DESCRIPTION_CHARS], "stopped": time.time()}
        box[0] = dict(sorted(roster.items(), key=lambda item: -_when(item[1]))[:ROSTER_LIMIT])
    return ident


def stopped_since(ctx: GitContext, chat: str, since: float) -> list[tuple[str, str]]:
    """(id, what it was sent to do) of each agent the chat stopped after `since`, in order."""
    roster = store.read_json(_roster(ctx, chat), default={}) if chat else {}
    if not isinstance(roster, dict):
        return []
    rows = sorted(((ident, row) for ident, row in roster.items() if _when(row) > since),
                  key=lambda item: _when(item[1]))
    return [(ident, str(row.get("description") or "")) for ident, row in rows]
