"""The work ledger: what is done, what is in flight, what is next.

One file per task, lifecycle encoded in the DIRECTORY, so a state transition is
`git mv` and N parallel sessions produce N distinct git adds and never a conflict.

    .claude/founder-os/plan/next/0007-export-csv.md
    .claude/founder-os/plan/doing/0004-fix-billing.md
    .claude/founder-os/plan/done/0001-scaffold.md

This is the shape four independent codebases converged on, and the single-blob
alternative is the one that provably breaks: five worktrees against one tasks.json
produce five overlapping hunks in the same JSON object and five identical generated
ids. The directory version produces five different filenames and five clean adds.

Claiming is what makes parallel work safe. A task in `doing/` carries the session that
owns it; a session that dies has its claims released by the reaper, so nothing stays
"in progress" forever — which is the state every surveyed tool leaves behind.

Ids are allocated against the union of every sibling worktree's files, not just this
one, because same-repository worktrees share the id namespace before their files are
ever committed.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import store
from .gitctx import GitContext

PLAN_DIR = "plan"
NEXT, DOING, DONE = "next", "doing", "done"
STATES = (NEXT, DOING, DONE)

MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 2_000


@dataclass
class Task:
    id: str
    title: str
    state: str
    path: Path
    owner: str = ""
    branch: str = ""
    created_at: str = ""
    updated_at: str = ""
    body: str = ""

    @property
    def number(self) -> int:
        return int(self.id) if self.id.isdigit() else 0


def plan_dir(ctx: GitContext, state: str = "") -> Path:
    return store.tier_a(ctx, PLAN_DIR, state) if state else store.tier_a(ctx, PLAN_DIR)


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _load(path: Path, state: str) -> Task | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _frontmatter(text)
    return Task(
        id=path.name.split("-", 1)[0],
        title=meta.get("title", path.stem),
        state=state,
        path=path,
        owner=meta.get("owner", ""),
        branch=meta.get("branch", ""),
        created_at=meta.get("created_at", ""),
        updated_at=meta.get("updated_at", ""),
        body=body,
    )


def load_all(ctx: GitContext, state: str = "") -> list[Task]:
    states = [state] if state else list(STATES)
    tasks: list[Task] = []
    for name in states:
        directory = plan_dir(ctx, name)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md")):
            task = _load(path, name)
            if task:
                tasks.append(task)
    return tasks


def sibling_worktrees(ctx: GitContext) -> list[Path]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(ctx.worktree_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    return [
        Path(line[len("worktree ") :])
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def next_id(ctx: GitContext) -> str:
    """Allocate against every sibling worktree, not just this one.

    Same-repository worktrees share the id namespace before their task files are ever
    committed, so an allocator that only looks locally hands the same number to five
    concurrent sessions.
    """
    highest = 0
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        for state in STATES:
            directory = root / store.TIER_A_DIRNAME / PLAN_DIR / state
            if not directory.is_dir():
                continue
            for path in directory.glob("[0-9][0-9][0-9][0-9]-*.md"):
                head = path.name.split("-", 1)[0]
                if head.isdigit():
                    highest = max(highest, int(head))
    return f"{highest + 1:04d}"


def slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return "-".join(words) or "task"


def _render(task_id: str, title: str, state: str, owner: str, branch: str, body: str) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [
        "---",
        f"id: {task_id}",
        f"title: {title[:MAX_TITLE_CHARS]}",
        f"state: {state}",
        f"owner: {owner}",
        f"branch: {branch}",
        f"created_at: {now}",
        f"updated_at: {now}",
        "---",
        "",
        body[:MAX_BODY_CHARS].strip() or "(no detail)",
        "",
    ]
    return "\n".join(lines)


def add(ctx: GitContext, title: str, body: str = "", branch: str = "") -> Task:
    task_id = next_id(ctx)
    path = plan_dir(ctx, NEXT) / f"{task_id}-{slug(title)}.md"
    store.atomic_write(path, _render(task_id, title, NEXT, "", branch, body), mode=0o644)
    return _load(path, NEXT)


def find(ctx: GitContext, task_id: str) -> Task | None:
    for task in load_all(ctx):
        if task.id == task_id.zfill(4) or task.id == task_id:
            return task
    return None


def _move(ctx: GitContext, task: Task, state: str, owner: str = "", branch: str = "") -> Task:
    """A state transition is a rename. Git records it as a rename, which merges cleanly."""
    target_dir = plan_dir(ctx, state)
    store.ensure_dir(target_dir)
    target = target_dir / task.path.name

    text = task.path.read_text(encoding="utf-8")
    meta, body = _frontmatter(text)
    updated = _render(
        task.id,
        meta.get("title", task.title),
        state,
        owner if owner or state == DOING else "",
        branch or meta.get("branch", ""),
        body,
    )
    store.atomic_write(target, updated, mode=0o644)
    if target != task.path:
        task.path.unlink(missing_ok=True)
    return _load(target, state)


def claim(ctx: GitContext, task_id: str, session_id: str, branch: str) -> tuple[Task | None, str]:
    """Take ownership. Returns (task, error). A task owned by a LIVE session is refused.

    Liveness is checked rather than assumed: a claim held by a crashed session is taken
    over, which is the difference between a work ledger and a graveyard.
    """
    from . import sessions

    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    if task.state == DONE:
        return None, f"task {task.id} is already done"

    if task.owner and task.owner != session_id:
        holder = sessions.get(ctx, task.owner)
        if holder and sessions.is_live(ctx, holder):
            return None, f"task {task.id} is held by live session {task.owner[:8]}"

    return _move(ctx, task, DOING, owner=session_id, branch=branch), ""


def complete(ctx: GitContext, task_id: str) -> tuple[Task | None, str]:
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    return _move(ctx, task, DONE), ""


def release(ctx: GitContext, session_id: str) -> int:
    """Return every task this session held to `next`. Called by the reaper."""
    released = 0
    for task in load_all(ctx, DOING):
        if task.owner == session_id:
            _move(ctx, task, NEXT)
            released += 1
    return released


def summary(ctx: GitContext) -> dict[str, int]:
    counts = {state: 0 for state in STATES}
    for task in load_all(ctx):
        counts[task.state] = counts.get(task.state, 0) + 1
    return counts


def render_for_board(ctx: GitContext, limit: int = 4) -> str:
    """The plan, compressed for injection.

    Deliberately shows `doing` before `next`: what is in flight right now is what a
    session must not collide with, and what is next is merely useful.
    """
    doing = load_all(ctx, DOING)
    upcoming = load_all(ctx, NEXT)[:limit]
    if not doing and not upcoming:
        return ""

    lines: list[str] = []
    if doing:
        lines.append("IN FLIGHT:")
        for task in doing:
            who = f" [{task.owner[:8]}]" if task.owner else " [unclaimed]"
            lines.append(f"  - {task.id} {task.title[:80]}{who}")
    if upcoming:
        lines.append("NEXT:")
        for task in upcoming:
            lines.append(f"  - {task.id} {task.title[:80]}")
    done = summary(ctx)[DONE]
    if done:
        lines.append(f"({done} done)")
    return "\n".join(lines)
