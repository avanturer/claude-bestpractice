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
    # Empty when the task file is in THIS checkout. The sibling's directory name
    # otherwise, so the board can say where the work actually is.
    worktree: str = ""

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
    """Every task on this clone, not just this checkout.

    Tier A lives inside the working tree, so each worktree carries its own copy on its
    own branch — which meant the ledger was per-worktree in a product whose whole premise
    is three to eight worktrees at once. A sibling's in-flight task was invisible and
    unclaimable: `founder-os plan` in one worktree listed one task, the other listed a
    different one, `claim` on a sibling's id said "no task 0002", and the board promised
    coordination while showing none of it. `next_id` already allocated across siblings,
    so the ids were consistent and only the reading was not.

    Deduplicated by FILENAME, keeping the most ADVANCED state. The same task exists in
    several worktrees whenever one branched from another, and the honest answer to "is
    anyone on 0002" is yes if any copy anywhere says doing.

    By filename and not by id, because ids genuinely collide across branches: `next_id`
    allocates against concurrent worktrees, not against a branch cut yesterday, so two
    unrelated tasks created on two branches from the same base are both 0002. Merging
    those branches is clean — different slugs, different filenames — and deduping by id
    would silently drop one of two real tasks on every such merge.
    """
    states = [state] if state else list(STATES)
    best: dict[str, Task] = {}
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        label = "" if root.resolve() == ctx.worktree_root.resolve() else root.name
        for task in _tasks_under(root, states, label):
            previous = best.get(task.path.name)
            if previous is None or _rank(task) > _rank(previous):
                best[task.path.name] = task
    return sorted(best.values(), key=lambda t: (STATES.index(t.state), t.id, t.title))


def _tasks_under(root: Path, states: list[str], label: str) -> list[Task]:
    """Every task file in one checkout, tagged with which checkout it came from."""
    out: list[Task] = []
    for name in states:
        directory = root / store.TIER_A_DIRNAME / PLAN_DIR / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md")):
            task = _load(path, name)
            if task:
                task.worktree = label
                out.append(task)
    return out


# doing outranks next outranks done: what is in flight is what a session must not
# collide with, and a local copy wins a tie so a claim acts on a file we own.
_ADVANCEMENT = {DOING: 3, NEXT: 2, DONE: 1}


def _rank(task: Task) -> tuple[int, int]:
    return (_ADVANCEMENT.get(task.state, 0), 0 if task.worktree else 1)


def sibling_worktrees(ctx: GitContext) -> list[Path]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(ctx.worktree_root),
        capture_output=True,
        encoding="utf-8", errors="surrogateescape",
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


ALLOC_LOCK = "plan-alloc.lock"


def add(ctx: GitContext, title: str, body: str = "", branch: str = "") -> Task:
    """Allocate an id and create the task under one lock.

    Scanning for the highest id and then writing the file is a read-modify-write, and
    eight sessions doing it at once is the operating mode this plugin is for. Unlocked,
    concurrent adds hand the same number to several tasks; the duplicates then merge
    cleanly, because one file per task is exactly what does not conflict — and after
    that `claim 0007` and `done 0007` silently act on whichever one `find` reaches
    first. The lock is held across both steps or it buys nothing.
    """
    with store.file_lock(store.tier_b(ctx, ALLOC_LOCK)):
        task_id = next_id(ctx)
        path = plan_dir(ctx, NEXT) / f"{task_id}-{slug(title)}.md"
        store.atomic_write(path, _render(task_id, title, NEXT, "", branch, body), mode=0o644)
    return _load(path, NEXT)


def find(ctx: GitContext, task_id: str) -> Task | None:
    for task in load_all(ctx):
        if task.id == task_id.zfill(4) or task.id == task_id:
            return task
    return None


def _move(task: Task, state: str, owner: str = "", branch: str = "") -> Task:
    """A state transition is a rename. Git records it as a rename, which merges cleanly.

    The rename happens where the FILE is, not where the caller is. Now that the ledger
    reads across siblings, `plan_dir(ctx, ...)` would have written the moved copy into
    this worktree while leaving the original in place — two files, one id, both claiming
    to be the truth, and the sibling still showing it unclaimed.
    """
    target_dir = task.path.parent.parent / state
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
    moved = _load(target, state)
    if moved:
        moved.worktree = task.worktree
    return moved


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

    return _move(task, DOING, owner=session_id, branch=branch), ""


def complete(ctx: GitContext, task_id: str) -> tuple[Task | None, str]:
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    return _move(task, DONE), ""


def release(ctx: GitContext, session_id: str) -> int:
    """Return every task this session held to `next`, in whichever worktree holds it.

    The reaper runs in a surviving session's worktree, but a dead session's task file
    lives in ITS worktree — and scanning only the local ledger, as this used to, left
    the work of every crashed sibling marked in flight forever. That is precisely the
    stuck-board failure the ledger exists to avoid, and the several-worktrees-at-once
    case is the normal one here rather than the exception.
    """
    released = 0
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        directory = root / store.TIER_A_DIRNAME / PLAN_DIR / DOING
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md")):
            task = _load(path, DOING)
            if task and task.owner == session_id:
                _move_to(path, root / store.TIER_A_DIRNAME / PLAN_DIR / NEXT, task)
                released += 1
    return released


def _move_to(path: Path, target_dir: Path, task: Task) -> None:
    """Rename a task file within the worktree that owns it, not the caller's."""
    store.ensure_dir(target_dir)
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    updated = _render(task.id, meta.get("title", task.title), NEXT, "", meta.get("branch", ""), body)
    store.atomic_write(target_dir / path.name, updated, mode=0o644)
    if target_dir / path.name != path:
        path.unlink(missing_ok=True)


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
    done = summary(ctx)[DONE]
    if not doing and not upcoming and not done:
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
    if done:
        # Shown even when nothing is in flight. A board that goes blank the moment work
        # finishes throws away the answer to "what has already been done here".
        lines.append(f"({done} done)")
    return "\n".join(lines)
