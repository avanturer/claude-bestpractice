"""The work ledger: what is done, what is in flight, what is next.

One file per task, lifecycle encoded in the DIRECTORY, so a state transition is
`git mv` and N parallel sessions produce N distinct git adds and never a conflict.

    .claude/claude-bestpractice/plan/next/0007-export-csv.md
    .claude/claude-bestpractice/plan/doing/0004-fix-billing.md
    .claude/claude-bestpractice/plan/done/0001-scaffold.md

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
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .gitctx import GitContext

PLAN_DIR = "plan"
NEXT, DOING, DONE = "next", "doing", "done"
# A fourth state, because "stopped and waiting on something" is not "queued". A task in
# `next` says pick me up; the same task blocked on a decision, an API key or somebody
# else's merge says the opposite, and conflating them sends session after session at work
# that cannot move. The blocker is mandatory for the same reason a handoff is: a pause
# nobody can lift is a task that has quietly left the ledger.
PAUSED = "paused"
STATES = (NEXT, DOING, PAUSED, DONE)

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
    # What has to become true for this to be finishable, and what is stopping it now.
    # Neither is decoration: a task with no `done_when` is closed on the model's own
    # judgement, which is the assertion decision 0002 refuses everywhere else; a pause
    # with no `blocker` cannot be resumed by anyone who was not in the room.
    done_when: str = ""
    blocker: str = ""
    created_at: str = ""
    updated_at: str = ""
    body: str = ""
    # The files the next session has to open. A task parked without them is a title and a
    # good intention: whoever picks it up spends their first ten minutes rediscovering
    # what the session that parked it already knew.
    paths: list[str] = field(default_factory=list)
    # The document this task was migrated out of, when it was. Without it "has this
    # registry been brought across?" is a question only a human can answer by reading
    # both — which is the question the whole migration has to answer mechanically.
    source: str = ""
    # How this task relates to the others. A research session routinely produces work
    # that is not independent: B is wrong until A lands, or two changes individually swing
    # the result the wrong way and only mean something shipped together. None of it was
    # expressible, so it went into a markdown section and was hoped to be read — the same
    # failure the ledger exists to end, one level up (#104).
    after: list[str] = field(default_factory=list)
    together: list[str] = field(default_factory=list)
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
        # Absent in every task written before this field existed, which is what `migrate`
        # backfills. Missing must read as "none named", never as a load failure.
        paths=[p.strip() for p in meta.get("paths", "").split(",") if p.strip()],
        source=meta.get("source", ""),
        done_when=meta.get("done_when", ""),
        blocker=meta.get("blocker", ""),
        after=_ids(meta.get("after", "")),
        together=_ids(meta.get("with", "")),
    )


def _ids(raw: str) -> list[str]:
    """Task ids out of a comma-separated field, normalised to how they are filed."""
    return [i.strip().zfill(4) for i in raw.split(",") if i.strip()]


def load_all(ctx: GitContext, state: str = "") -> list[Task]:
    """Every task on this clone, not just this checkout.

    Tier A lives inside the working tree, so each worktree carries its own copy on its
    own branch — which meant the ledger was per-worktree in a product whose whole premise
    is three to eight worktrees at once. A sibling's in-flight task was invisible and
    unclaimable: `claude-bp plan` in one worktree listed one task, the other listed a
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
    best: dict[str, Task] = {}
    # Always every state, even when one was asked for. Scanning only the requested
    # directory cannot see that a sibling has moved the task on, so the dedup above never
    # runs: `startable` counted a task this worktree had closed, because it looked in
    # `next/` alone and found the stale copy (#123).
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        label = "" if root.resolve() == ctx.worktree_root.resolve() else root.name
        for task in _tasks_under(root, list(STATES), label):
            previous = best.get(task.path.name)
            if previous is None or _rank(task) > _rank(previous):
                best[task.path.name] = task
    resolved = [t for t in best.values() if not state or t.state == state]
    return sorted(resolved, key=lambda t: (STATES.index(t.state), t.id, t.title))


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


# Lifecycle order, because a task file only ever moves forward: the copy that has travelled
# furthest is the one that was written last, and that is the truth when copies disagree.
#
# `next` used to outrank `done`, to keep a stale closure from hiding work in flight. The
# cost was the board: closing a task in your own worktree was outvoted by every sibling
# still carrying the old `next` copy, so with ten worktrees ten closed tasks all came back
# as NEXT and stayed there. Reported as a board that cannot be read (#123).
#
# The trade, stated: a task closed by mistake in one tree now hides an active `doing` copy
# in another. That costs the session working on it nothing — it holds its own file — and
# it costs the board one wrong row, against a board that was wrong about everything ever
# closed. A local copy still wins a tie, so a claim acts on a file we own.
_ADVANCEMENT = {DONE: 4, PAUSED: 3, DOING: 2, NEXT: 1}


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


def _render(task_id: str, title: str, state: str, owner: str, branch: str, body: str,
            paths: list[str] | None = None, source: str = "",
            done_when: str = "", blocker: str = "", created_at: str = "",
            after: list[str] | None = None, together: list[str] | None = None) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [
        "---",
        f"id: {task_id}",
        f"title: {title[:MAX_TITLE_CHARS]}",
        f"state: {state}",
        f"owner: {owner}",
        f"branch: {branch}",
        f"paths: {', '.join(paths or [])}",
        f"source: {source}",
        f"done_when: {done_when[:MAX_TITLE_CHARS]}",
        f"blocker: {blocker[:MAX_TITLE_CHARS]}",
        f"after: {', '.join(after or [])}",
        f"with: {', '.join(together or [])}",
        # Preserved across a move. Rewriting it on every transition made every task look
        # created at the moment it was last touched, which is the one thing `created_at`
        # is for.
        f"created_at: {created_at or now}",
        f"updated_at: {now}",
        "---",
        "",
        body[:MAX_BODY_CHARS].strip() or "(no detail)",
        "",
    ]
    return "\n".join(lines)


MIN_HANDOFF_CHARS = 80

# Shorter than a handoff on purpose: a blocker is one fact, not a briefing.
MIN_BLOCKER_CHARS = 12


def handoff_problems(paths: list[str], note: str) -> list[str]:
    """Why this is not yet a handoff somebody else could pick up.

    A parked task is read by a session that was not in the room. It has the title and
    nothing else — not the reasoning, not the files, not what was already ruled out — so
    a thin one costs its reader the whole rediscovery the parking session was trying to
    save. Refusing here is the same trade the evidence gate makes: a moment now against
    an hour later.
    """
    problems = []
    if not paths:
        problems.append("no files named — the next session has nowhere to start")
    if len(" ".join(note.split())) < MIN_HANDOFF_CHARS:
        problems.append(
            f"the note is under {MIN_HANDOFF_CHARS} characters — say what is already known, "
            "what was ruled out, and where it stands"
        )
    return problems


ALLOC_LOCK = "plan-alloc.lock"


FROM_THE_FOUNDER = "the founder's message"


def open_for(ctx: GitContext, statement: str, session_id: str) -> Task | None:
    """Put the founder's instruction on the board the moment it arrives. None if one is.

    The demand fired at the first WRITE, so between "the founder gave a task" and "the
    session touched a file" the board said nothing — and every sibling deciding what was
    safe to touch read an empty board while somebody was already working. In practice the
    card got filed because a gate refused, which makes it a description of work already
    done rather than a claim on work about to happen.

    Filed into NEXT and deliberately NOT claimed. Claiming requires `done_when` and the
    paths, and neither is knowable before the session has looked at anything — a card
    guessed at that moment is worse than a late one, which is why `claim` refuses an
    unplanned task. So the board learns WHO and WHAT immediately, and the plan is filled
    in with `update` once it is real.

    One per session. A founder who sends three messages about one task gets one card, not
    three, because the ledger is only worth reading while it does not drift.
    """
    if not statement.strip():
        return None
    for task in load_all(ctx, DOING):
        if task.owner == session_id:
            return None
    for task in load_all(ctx, NEXT):
        if task.source == FROM_THE_FOUNDER and task.branch == ctx.branch:
            return None
    return add(ctx, statement.strip().splitlines()[0][:120], branch=ctx.branch,
               source=FROM_THE_FOUNDER)


def add(ctx: GitContext, title: str, body: str = "", branch: str = "",
        paths: list[str] | None = None, done_when: str = "", source: str = "",
        after: list[str] | None = None, together: list[str] | None = None) -> Task:
    """Allocate an id and create the task under one lock.

    Scanning for the highest id and then writing the file is a read-modify-write, and
    eight sessions doing it at once is the operating mode this plugin is for. Unlocked,
    concurrent adds hand the same number to several tasks; the duplicates then merge
    cleanly, because one file per task is exactly what does not conflict — and after
    that `claim 0007` and `done 0007` silently act on whichever one `find` reaches
    first. The lock is held across both steps or it buys nothing.
    """
    return park(ctx, title, body=body, branch=branch, paths=paths or [], source=source,
                done_when=done_when, after=after, together=together)


def park(ctx: GitContext, title: str, body: str = "", branch: str = "",
         paths: list[str] | None = None, source: str = "", done_when: str = "",
         after: list[str] | None = None, together: list[str] | None = None) -> Task:
    """Hand a task to a session that has not happened yet.

    The scene this exists for: a chat with more work in it than belongs in one chat, and
    the founder saying "leave that for another session". Before this the answer was a
    markdown file somebody invented on the spot — which is a second task system, in a
    repository that already has one, with nothing keeping the two honest.
    """
    with store.file_lock(store.tier_b(ctx, ALLOC_LOCK)):
        task_id = next_id(ctx)
        path = plan_dir(ctx, NEXT) / f"{task_id}-{slug(title)}.md"
        store.atomic_write(
            path,
            _render(task_id, title, NEXT, "", branch, body, paths, source,
                    done_when=done_when, after=after, together=together),
            mode=0o644
        )
    return _load(path, NEXT)


def _labelled(heading: str, body: list[str]) -> list[str]:
    """One optional block of `show`, or nothing when it has nothing to say."""
    return [heading, *body, ""] if body else []


def _order_lines(task: Task) -> list[str]:
    out = []
    if task.after:
        out.append(f"  not until {', '.join(task.after)} has landed")
    if task.together:
        out.append(f"  ships in the same change as {', '.join(task.together)}")
    return out


def show(task: Task) -> str:
    """Everything the next session needs, in one read.

    Deliberately NOT on the board. The board is injected into every session and pays for
    itself every time; a full handoff is wanted by exactly one session, the one picking
    this up, and putting it in front of the other seven is how a context budget dies.
    """
    lines = [f"{task.id}  {task.title}", ""]
    lines += _labelled("FILES:", [f"  - {p}" for p in task.paths])
    lines += _labelled("DONE WHEN:", [f"  {task.done_when}"] if task.done_when else [])
    lines += _labelled("WAITING ON:", [f"  {task.blocker}"] if task.blocker else [])
    lines += _labelled("ORDER:", _order_lines(task))
    lines.append("HANDOFF:")
    lines += [f"  {line}" for line in (task.body or "(no detail)").splitlines()]
    if task.branch:
        lines += ["", f"parked from branch {task.branch}"]
    return "\n".join(lines)


def find(ctx: GitContext, task_id: str) -> Task | None:
    for task in load_all(ctx):
        if task.id == task_id.zfill(4) or task.id == task_id:
            return task
    return None


def _move(task: Task, state: str, owner: str = "", branch: str = "",
          blocker: str | None = None) -> Task:
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
        # Carried, not dropped. A transition that forgets the files and the finish
        # condition hands the next session the thin task the ledger exists to prevent.
        [p.strip() for p in meta.get("paths", "").split(",") if p.strip()],
        meta.get("source", ""),
        meta.get("done_when", ""),
        meta.get("blocker", "") if blocker is None else blocker,
        meta.get("created_at", ""),
        _ids(meta.get("after", "")),
        _ids(meta.get("with", "")),
    )
    store.atomic_write(target, updated, mode=0o644)
    if target != task.path:
        task.path.unlink(missing_ok=True)
    moved = _load(target, state)
    if moved:
        moved.worktree = task.worktree
    return moved


IDLE_HOURS = 24.0


def _stale_for(task: Task, now: float, hours: float) -> float:
    """Hours since this task last moved, or 0 when that cannot be read."""
    stamp = task.updated_at or task.created_at
    try:
        moved = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0
    idle = (now - (moved - time.timezone)) / 3600.0
    return idle if idle > hours else 0.0


def _still_on_it(ctx: GitContext, task: Task) -> bool:
    """Is the owner touching this task's own files right now?

    The reason this is not just a clock. A session can hold one task for two days and be
    working on it the whole time; reclaiming that would take work off somebody mid-change,
    which is worse than the stale row it was meant to fix. So the clock only decides for a
    task whose OWNER has moved on to other files.
    """
    from . import sessions

    holder = sessions.get(ctx, task.owner) if task.owner else None
    if holder is None or not sessions.is_live(ctx, holder):
        return False
    if not task.paths:
        return not sessions.is_idle(holder)
    return any(touched in task.paths for touched in holder.last_touched)


def sweep_idle(ctx: GitContext, hours: float = IDLE_HOURS) -> list[Task]:
    """Return work that stopped moving to the queue, and say so in the task itself.

    `reap` already releases the tasks of a session that DIED. Nothing covered the commoner
    case: a live chat that claimed 0007, moved on to something else, and left it reading
    `doing` on every board for the rest of the week. The board's whole claim is that it
    says what is in flight, and a row nobody is working on is the claim being false.

    Back to `next`, not to `paused`: paused means waiting on something nameable, and this
    is waiting on nobody. It goes back where anyone can pick it up, carrying a line saying
    what happened so the next session does not rediscover it.
    """
    now = time.time()
    moved: list[Task] = []
    for task in load_all(ctx, DOING):
        idle = _stale_for(task, now, hours)
        if not idle or _still_on_it(ctx, task):
            continue
        note = (f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}] returned to the "
                f"queue: claimed by {task.owner[:8] or 'nobody'} and untouched for "
                f"{int(idle)}h.")
        task.body = f"{task.body}\n\n{note}".strip() if task.body else note
        _rewrite_body(task)
        released = _move(task, NEXT)
        if released:
            moved.append(released)
    return moved


# A queue is only an answer to "what can I start" while somebody would actually start it.
# `sweep_idle` moves `doing` back to `next` and nothing has ever moved anything OUT, so
# `next` is a one-way sink: every card ever filed and not done is still standing in it.
# Empty in this repository and 60+ deep in the one the founder actually builds in.
QUEUE_STALE_DAYS = 21.0


def sweep_queue(ctx: GitContext, days: float = QUEUE_STALE_DAYS) -> list[Task]:
    """Set aside queued work nobody has picked up, with the reason written on it.

    To `paused`, which is the state for waiting on something nameable — and this is
    waiting on the one thing that is always nameable: somebody deciding it still matters.
    `sweep_idle` refuses `paused` for the opposite case and is right to; work that was in
    flight is waiting on nobody and belongs where anyone can take it.

    Deleting is not on the table. `claude-bp-plan resume` brings it straight back, and the
    file was never removed — so the cost of being wrong here is one command.
    """
    if days <= 0:
        return []
    now = time.time()
    moved: list[Task] = []
    for task in load_all(ctx, NEXT):
        idle = _stale_for(task, now, days * 24.0)
        if not idle or task.owner:
            continue
        blocker = f"nobody picked this up in {int(idle // 24)}d — resume it if it still matters"
        parked = _move(task, PAUSED, blocker=blocker)
        if parked:
            moved.append(parked)
    return moved


def _rewrite_body(task: Task) -> None:
    """Persist an amended body in place, leaving the frontmatter as it stands."""
    meta, _ = _frontmatter(task.path.read_text(encoding="utf-8"))
    head = "\n".join(f"{k}: {v}" for k, v in meta.items())
    store.atomic_write(task.path, f"---\n{head}\n---\n\n{task.body}\n", mode=0o644)


def blockers(ctx: GitContext, task: Task) -> list[str]:
    """The ids this task named with `--after` that have not landed yet.

    Not done is blocking, including an id that names nothing: a task waiting on `0035`
    when no `0035` exists is waiting forever, and reading that as clear would be the
    silent failure rather than the visible one.
    """
    if not task.after:
        return []
    landed = {t.id for t in load_all(ctx) if t.state == DONE}
    return [wanted for wanted in task.after if wanted not in landed]


def relations(ctx: GitContext, task: Task) -> str:
    """What `list` shows beside a task so "can I start this" needs no design document."""
    notes = []
    waiting = blockers(ctx, task)
    if waiting:
        notes.append(f"after {', '.join(waiting)}")
    if task.together:
        notes.append(f"with {', '.join(task.together)}")
    return "; ".join(notes)


def startable(ctx: GitContext) -> list[Task]:
    """Queued work with nothing in front of it — the answer to "what can I start now"."""
    return [t for t in load_all(ctx, NEXT) if not blockers(ctx, t)]


def activity(ctx: GitContext, task: Task) -> str:
    """Whether a chat is working on this RIGHT NOW, derived rather than stored.

    Stored activity is the crutch: a flag saying "in progress" is written by a session that
    then crashes, and it stays true forever — which is precisely the case the reader needs
    it for. The session registry already knows who is alive and when they were last seen,
    so the ledger asks it instead of keeping a second copy that can disagree.

    Three answers, and the third is the one that was missing entirely. The board printed
    the owner's id and nothing about it, so a task a live chat is editing this minute and
    one abandoned by a crashed session three days ago looked identical.
    """
    if task.state != DOING or not task.owner:
        return ""
    from . import sessions

    holder = sessions.get(ctx, task.owner)
    if holder is None:
        return f"claimed by {task.owner[:8]}, which has no record — reclaimable"
    if not sessions.is_live(ctx, holder):
        return f"held by {task.owner[:8]}, which is gone — reclaimable"
    idle = max(time.time() - float(holder.heartbeat_at or 0), 0)
    return f"active in {task.owner[:8]}, seen {_ago(idle)} ago"


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def pause(ctx: GitContext, task_id: str, blocker: str) -> tuple[Task | None, str]:
    """Stop work and say what would restart it.

    The blocker is required. "Paused" without one is indistinguishable from abandoned, and
    the next session has no way to tell whether it is waiting on a decision, a credential,
    somebody else's merge, or nothing at all.
    """
    if len(blocker.strip()) < MIN_BLOCKER_CHARS:
        return None, (
            "a pause needs to say what would lift it — name the decision, the credential, "
            "the merge or the answer this is waiting on"
        )
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    if task.state == DONE:
        return None, f"task {task.id} is already done"
    return _move(task, PAUSED, blocker=blocker.strip()), ""


def resume(ctx: GitContext, task_id: str) -> tuple[Task | None, str]:
    """Return a paused task to the queue, clearing the blocker that held it."""
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    if task.state != PAUSED:
        return None, f"task {task.id} is not paused"
    return _move(task, NEXT, blocker=""), ""


def amend(ctx: GitContext, task_id: str, note: str = "", paths: list[str] | None = None,
          done_when: str = "") -> tuple[Task | None, str]:
    """Update what a task knows without changing which task it is.

    A task learns things while it waits — a file turns out to be the wrong one, a
    condition gets sharper. Without this the only ways to record that were to park a
    second task, which splits the identity, or to rewrite the file by hand, which the
    worktree gate refuses from the main checkout.
    """
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    meta, body = _frontmatter(task.path.read_text(encoding="utf-8"))
    updated = _render(
        task.id, meta.get("title", task.title), task.state, task.owner, task.branch,
        note.strip() or body,
        paths if paths is not None else task.paths,
        meta.get("source", ""),
        done_when.strip() or task.done_when,
        task.blocker,
        meta.get("created_at", ""),
    )
    store.atomic_write(task.path, updated, mode=0o644)
    return _load(task.path, task.state), ""


def _unplanned(task: Task) -> str:
    """Why this card cannot be started yet, or "" when it carries a plan.

    Separate from `claim` so the rule reads as one thing rather than as four branches
    inside a function that is also about ownership and liveness.
    """
    fixes = {
        "--done-when": '--done-when "<what has to become true>"',
        "--paths": "--paths <files you expect to touch>",
    }
    missing = [
        need for need, got in (("--done-when", task.done_when.strip()), ("--paths", task.paths))
        if not got
    ]
    if not missing:
        return ""
    how = " ".join(fixes[need] for need in missing)
    return (
        f"task {task.id} cannot be started without a plan: {' and '.join(missing)}.\n"
        f"  claude-bp-plan update {task.id} {how}\n"
        "One line each is enough. The paths are what the drift gate measures against."
    )


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

    # The plan, demanded where the plan has to exist. `pre-tool` already refuses a write
    # that no claimed card covers, so requiring it HERE is what makes "no code without a
    # plan" binding — and it costs the founder nothing, where the harness's own plan mode
    # ends in an approval dialog they spent several releases removing.
    #
    # At `claim` and not at `add`: filing a rough card has to stay a single line, or the
    # board stops being written to. Starting one is the moment the plan is owed.
    if task.owner != session_id:
        unplanned = _unplanned(task)
        if unplanned:
            return None, unplanned

    return _move(task, DOING, owner=session_id, branch=branch), ""


def complete(ctx: GitContext, task_id: str) -> tuple[Task | None, str]:
    """Close a card. The finish condition is demanded at `claim`, not here.

    v1.26.0 demanded it at this end, which was the right rule at the wrong moment: a card
    that reaches `doing` has been through `claim`, so by the time anything is closed the
    condition already exists, and the check here could only ever fire for a file edited by
    hand. Asked where the plan is owed instead — before the work, not after it.
    """
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    landed = _move(task, DONE)

    # Whoever was waiting on this is waiting right now, in a session that will not be
    # restarted for hours. `startable` already answers "what can begin"; nobody reads it
    # again once they have decided they are blocked, so the answer has to travel to them.
    from . import inbox, sessions

    live = {s.session_id for s in sessions.live_sessions(ctx)}
    for waiting in load_all(ctx):
        if task_id in waiting.after and waiting.owner in live and not blockers(ctx, waiting):
            inbox.post(
                ctx, waiting.owner,
                f"{task_id} is done — {waiting.id} is no longer blocked.",
                sender=task_id,
            )
    return landed, ""


def release(ctx: GitContext, session_id: str) -> int:
    """Return every task this session held to `next`, in whichever worktree holds it.

    The reaper runs in a surviving session's worktree, but a dead session's task file
    lives in ITS worktree — and scanning only the local ledger, as this used to, left
    the work of every crashed sibling marked in flight forever. That is precisely the
    stuck-board failure the ledger exists to avoid, and the several-worktrees-at-once
    case is the normal one here rather than the exception.
    """
    released = 0
    freed: list[str] = []
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        directory = root / store.TIER_A_DIRNAME / PLAN_DIR / DOING
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md")):
            task = _load(path, DOING)
            if task and task.owner == session_id:
                _move_to(path, root / store.TIER_A_DIRNAME / PLAN_DIR / NEXT, task)
                released += 1
                freed.append(task.id)
    if freed:
        _remember_release(ctx, freed, session_id)
    return released


# Who held what, so a session that comes back can be given it back. Tier B, because this
# is coordination state rather than truth about the work: it dies with the clone, and
# losing it costs one manual re-claim rather than a wrong ledger (decision 0001).
RELEASED_FILE = "released-claims.json"


def _remember_release(ctx: GitContext, task_ids: list[str], session_id: str) -> None:
    with store.guarded_json(store.tier_b(ctx, RELEASED_FILE), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        for task_id in task_ids:
            table[task_id] = {"session_id": session_id, "at": time.time()}
        box[0] = table


def reclaim(ctx: GitContext, session_id: str) -> list[str]:
    """Give a returning session back the work the reaper took from it.

    A process restart — VS Code closed, WSL fell over, a resume after compaction — leaves
    the session id unchanged and the pid dead, so a sibling's reaper releases the claim
    correctly and the session comes back owning nothing. The first Stop then refuses the
    turn for having no task on the board, and the demand it prints suggests filing a NEW
    one, which is how a board grows duplicates of work already on it (#131).

    Through `claim`, which already refuses a task held by a LIVE session — so a returning
    session cannot take back work a sibling picked up, and can take back work a sibling
    picked up and then died holding. A stricter check here was tried and removed: it
    duplicated that rule and got the dead-holder case wrong in the process.

    Each memory is spent whether or not it was used, so a task legitimately re-planned
    weeks later is never silently pulled back into a session that has moved on.
    """
    taken: list[str] = []
    with store.guarded_json(store.tier_b(ctx, RELEASED_FILE), default={}) as box:
        table = box[0] if isinstance(box[0], dict) else {}
        mine = [tid for tid, row in table.items()
                if isinstance(row, dict) and row.get("session_id") == session_id]
        for task_id in mine:
            task, _ = claim(ctx, task_id, session_id, ctx.branch)
            if task is not None:
                taken.append(task_id)
            table.pop(task_id, None)
        box[0] = table
    return taken


def _move_to(path: Path, target_dir: Path, task: Task) -> None:
    """Rename a task file within the worktree that owns it, not the caller's."""
    store.ensure_dir(target_dir)
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    # Everything, not just the title. Reclaiming a crashed session's task rewrote the
    # document without its files, its finish condition or its relations — handing the
    # next session the thin task the ledger exists to prevent, at the exact moment it
    # has least context.
    updated = _render(
        task.id, meta.get("title", task.title), NEXT, "", meta.get("branch", ""), body,
        [p.strip() for p in meta.get("paths", "").split(",") if p.strip()],
        meta.get("source", ""), meta.get("done_when", ""), "", meta.get("created_at", ""),
        _ids(meta.get("after", "")), _ids(meta.get("with", "")),
    )
    store.atomic_write(target_dir / path.name, updated, mode=0o644)
    if target_dir / path.name != path:
        path.unlink(missing_ok=True)


def summary(ctx: GitContext) -> dict[str, int]:
    counts = {state: 0 for state in STATES}
    for task in load_all(ctx):
        counts[task.state] = counts.get(task.state, 0) + 1
    return counts


def _section(heading: str, tasks: list[Task], note) -> list[str]:
    """One block of the board, or nothing when its state is empty."""
    if not tasks:
        return []
    return [heading] + [f"  - {t.id} {t.title[:80]}{note(t)}" for t in tasks]


def render_for_board(ctx: GitContext, limit: int = 4) -> str:
    """The plan, compressed for injection.

    Deliberately shows `doing` before `next`: what is in flight right now is what a
    session must not collide with, and what is next is merely useful.
    """
    doing = load_all(ctx, DOING)
    upcoming = load_all(ctx, NEXT)[:limit]
    paused = load_all(ctx, PAUSED)
    done = summary(ctx)[DONE]
    # `paused` belongs in this test. Without it a repository whose only work is blocked
    # rendered an empty board — the one state where the reader most needs to be told
    # something, reported as nothing at all.
    if not doing and not upcoming and not paused and not done:
        return ""

    lines: list[str] = []
    lines += _section(
        "IN FLIGHT:", doing, lambda task: f" [{activity(ctx, task) or 'unclaimed'}]")
    # Separated from NEXT deliberately: these are not work to pick up, they are work
    # waiting on something, and mixing them sends sessions at tasks that cannot move.
    lines += _section(
        "PAUSED:", paused, lambda task: f" [waiting: {task.blocker[:60]}]")
    lines += _section("NEXT:", upcoming, lambda task: "")
    if done:
        # Shown even when nothing is in flight. A board that goes blank the moment work
        # finishes throws away the answer to "what has already been done here".
        lines.append(f"({done} done)")
    return "\n".join(lines)
