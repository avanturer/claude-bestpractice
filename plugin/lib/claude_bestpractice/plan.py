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

# What a card says when nobody said anything. Named rather than spelled out twice, because
# a reader that asks "has this card been written to?" has to compare against it — and a
# literal in two files drifts the moment one of them is reworded, leaving that reader
# quietly answering yes for every card ever filed.
NO_DETAIL = "(no detail)"

# The front-matter key naming the session a founder's message opened a card for.
OPENED_BY = "opened_by"


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
    # The harness session whose founder's message opened this card — the one session whose
    # later messages may retitle it while it sits unclaimed. Empty on every other card.
    opened_by: str = ""

    @property
    def number(self) -> int:
        return int(self.id) if self.id.isdigit() else 0


def plan_dir(ctx: GitContext, state: str = "") -> Path:
    """Where a NEW task file is created: the main checkout, whichever tree is asking.

    The board is one board per project, and it was one board per WORKTREE. Reading has
    unioned the siblings since #123, so a task added in a worktree could be listed from
    anywhere — but the file existed in that worktree and nowhere else, so `git worktree
    remove` destroyed it. Measured on a fixture: listed from the main checkout before the
    removal, gone from every tree after it. Fourteen tasks were nearly lost that way, and
    were kept only by `git add -f` by hand (#200).

    Committing does not save them either in the repository that reported this: a global
    ignore rule covers `.claude/claude-bestpractice/`, which the plugin's own health line
    already reports, so the files were never staged and the worktree was their only copy.

    The main checkout is the one tree in a clone that outlives every other, which is why
    it is where the ledger lives. Transitions still happen where the FILE is — `_move`
    follows it — so a task already sitting in a worktree keeps working, and the repair
    carries it over on the next session start there.

    Never fails an `add`: a clone whose trees cannot be listed falls back to this one,
    because a task written somewhere awkward is recoverable and a task refused is not.
    """
    from . import worktree

    try:
        root = worktree.main_checkout(ctx)
    except Exception:  # noqa: BLE001 - an unlistable clone still has to accept a task
        root = ctx.worktree_root
    base = root.joinpath(store.TIER_A_DIRNAME, PLAN_DIR)
    return base / state if state else base


def named_for(ctx: GitContext, path: Path) -> str:
    """How to name a ledger file to somebody standing in THIS worktree.

    Relative while the file is under the tree the session is in, which is every repository
    with no worktrees and so nearly every line this ever prints. Absolute once the ledger
    root and the session's tree are two different directories, because that is the case
    where a relative path is a pointer to nowhere: `add` from a worktree returned
    `.claude/claude-bestpractice/plan/next/0241-….md`, and no such file exists in the tree
    the reader is standing in.

    It crashed rather than misleading — `relative_to` raises on a path outside the root —
    which is how #202 was found one release after `plan_dir` moved the ledger into the main
    checkout (#200). The traceback came AFTER the file was written and the id was printed,
    so the task existed and the command still failed; a caller that only wants to name a
    file it just wrote should never be able to fail at all.
    """
    try:
        return path.relative_to(ctx.worktree_root).as_posix()
    except ValueError:
        return str(path)


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
        opened_by=meta.get(OPENED_BY, ""),
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
            after: list[str] | None = None, together: list[str] | None = None,
            opened_by: str = "") -> str:
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
        # Only on the cards it concerns, so every other card keeps the shape it has always had.
        *([f"{OPENED_BY}: {opened_by}"] if opened_by else []),
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
        body[:MAX_BODY_CHARS].strip() or NO_DETAIL,
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


def opened_for(ctx: GitContext, opener: str) -> Task | None:
    """The unclaimed card a founder's message opened for this session, if there is one."""
    if not opener:
        return None
    for task in load_all(ctx, NEXT):
        if task.source == FROM_THE_FOUNDER and task.opened_by == opener:
            return task
    return None


def open_for(ctx: GitContext, statement: str, session_id: str, opener: str = "") -> Task | None:
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

    `opener` is the harness session id, which survives the move into a worktree that
    `session_id` does not. It used to be the BRANCH that decided whose card this was, and
    every session starts in the main checkout on the trunk: the second session's first
    message retitled the first session's card, and the first session's work left the board.
    """
    if not statement.strip():
        return None
    opener = opener or session_id
    # Held under any id this session has had. Claimed in the main checkout and spoken to
    # again from its own tree, it is one session on one card — and the founder's next line
    # filed a second card for work already on the board, the drift #131 describes.
    if held_by(ctx, session_id):
        return None
    said = statement.strip().splitlines()[0][:120]
    mine = opened_for(ctx, opener)
    if mine is not None:
        # FOLLOWS the founder while nobody has claimed it. Their first message is often a
        # remark rather than the work — «потом как все задачи на доске доделаю» cleared the
        # bar and sat on the board as a task, which is the drift the ledger exists not to
        # have. The statement itself already follows them; the card had no reason not to
        # (#170).
        #
        # Only while UNCLAIMED. Once a session has claimed it, that session wrote a plan —
        # a `done_when` and the paths — and overwriting its title with whatever was said
        # next would clobber work with conversation.
        if mine.title != said:
            amend(ctx, mine.id, title=said)
        return None
    return add(ctx, said, branch=ctx.branch, source=FROM_THE_FOUNDER, opened_by=opener)


def add(ctx: GitContext, title: str, body: str = "", branch: str = "",
        paths: list[str] | None = None, done_when: str = "", source: str = "",
        after: list[str] | None = None, together: list[str] | None = None,
        opened_by: str = "") -> Task:
    """Allocate an id and create the task under one lock.

    Scanning for the highest id and then writing the file is a read-modify-write, and
    eight sessions doing it at once is the operating mode this plugin is for. Unlocked,
    concurrent adds hand the same number to several tasks; the duplicates then merge
    cleanly, because one file per task is exactly what does not conflict — and after
    that `claim 0007` and `done 0007` silently act on whichever one `find` reaches
    first. The lock is held across both steps or it buys nothing.
    """
    return park(ctx, title, body=body, branch=branch, paths=paths or [], source=source,
                done_when=done_when, after=after, together=together, opened_by=opened_by)


def park(ctx: GitContext, title: str, body: str = "", branch: str = "",
         paths: list[str] | None = None, source: str = "", done_when: str = "",
         after: list[str] | None = None, together: list[str] | None = None,
         opened_by: str = "") -> Task:
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
                    done_when=done_when, after=after, together=together,
                    opened_by=opened_by),
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
    lines += [f"  {line}" for line in (task.body or NO_DETAIL).splitlines()]
    if task.branch:
        lines += ["", f"parked from branch {task.branch}"]
    return "\n".join(lines)


def find(ctx: GitContext, task_id: str) -> Task | None:
    for task in load_all(ctx):
        if task.id == task_id.zfill(4) or task.id == task_id:
            return task
    return None


def copies(ctx: GitContext, task_id: str) -> list[Task]:
    """Every file in this clone that IS this task — one per worktree carrying a copy.

    `find` answers "which copy speaks for this task"; this answers "which files have to
    move when it changes state". They were the same question while a ledger had one copy,
    and they stopped being the same the moment reading unioned the siblings (#123): a
    transition moved the copy `find` returned and left the others exactly where they were,
    so `done` printed success while the board went on showing the task as waiting, and
    `list` read the stale copy back (#212).

    Undeduplicated on purpose. `load_all` keeps the most advanced copy per filename, which
    is the right answer for a reader and the wrong one for a writer — the copies it hides
    are precisely the ones left behind.

    Matched by id and not by filename, because the same task carries a different slug in a
    tree where its title was amended, and those copies are the ones that outlive the board.
    """
    wanted = task_id.zfill(4)
    out: list[Task] = []
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        label = "" if root.resolve() == ctx.worktree_root.resolve() else root.name
        for task in _tasks_under(root, list(STATES), label):
            if task.id in (wanted, task_id):
                out.append(task)
    # This tree first, so a caller that reports one file reports the one the reader can see.
    return sorted(out, key=lambda t: (1 if t.worktree else 0, t.worktree, t.path.name))


def reconcile_copies(ctx: GitContext) -> int:
    """Bring every copy of every task up to the state the board already shows it in.

    The repair for ledgers older transitions scattered: they moved the copy the command
    could see and no other, so the files disagreed while the board — which keeps the most
    advanced copy — read correctly, right up until the tree holding the advanced copy was
    removed and the task came back from the dead (#210, #212).

    Forward only, because a task file only ever moves forward: this can bring a stale copy
    up to a closure, never undo one.
    """
    moved = 0
    for task in load_all(ctx):
        for copy in copies(ctx, task.id):
            if STATES.index(copy.state) >= STATES.index(task.state):
                continue
            if _move(copy, task.state, owner=copy.owner, branch=copy.branch) is not None:
                moved += 1
    return moved


def _move_every(ctx: GitContext, task_id: str, state: str, **kwargs) -> Task | None:
    """Carry one transition to every copy of the task, and answer with the nearest one.

    A closure that reaches one worktree is a closure that dies with `git worktree remove`
    — reported as tasks coming back as `next` after the tree that closed them was removed
    (#210). Moving every copy makes the transition a fact about the repository rather than
    about the directory the command happened to be run from.
    """
    landed: Task | None = None
    for copy in copies(ctx, task_id):
        moved = _move(copy, state, **kwargs)
        if moved is not None and (landed is None or (landed.worktree and not copy.worktree)):
            landed = moved
    return landed


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run one git command in a tree, answering with its status and output.

    Separate from `gitctx._run` on purpose: that module promises never to shell out to
    anything that mutates the repository, and the calls below write the index. The
    promise is worth keeping where it is made, so the writing lives here.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            encoding="utf-8", errors="surrogateescape",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def follow_in_git(source: Path, target: Path) -> bool:
    """Carry a TRACKED task file's move into the index, so git records a rename.

    A transition has always been a rename on disk and nothing in git, which is invisible
    while both paths look the same to git and a disaster the moment they do not. The
    founder's global ignore covers `.claude/claude-bestpractice/`, and `plan/paused/` was
    never committed, so pausing a task committed earlier deleted a tracked file and
    created an ignored one: `git status` showed a bare `D` with no counterpart anywhere,
    fifty times over, and every one of them looked like lost work (#208).

    Only ever for a file git is ALREADY tracking. Adding an ignored file the founder never
    committed would be the plugin granting itself a place in their history, which is the
    line decision 0008 draws; preserving what they already track is the opposite.

    `add -f` is what makes it work at all: without the force the target is ignored and the
    add is a silent no-op, which is exactly the bug. Never raises, and answers whether the
    index actually moved — a ledger transition that fails because git is busy is a task
    the founder cannot pause.
    """
    tree = target.parent
    if not _tracked(source, tree):
        return False
    if _git(["add", "-f", "--", str(target)], tree)[0] != 0:
        return False
    return _git(["add", "-A", "--", str(source)], tree)[0] == 0


def _tracked(path: Path, tree: Path | None = None) -> bool:
    """Is git holding this file in the index of the checkout it sits in?

    Asked in the file's OWN directory unless the caller names a tree, because a worktree
    has its own index and asking the wrong one about a file answers no for a file that is
    tracked.
    """
    code, out = _git(["ls-files", "--error-unmatch", "--", str(path)], tree or path.parent)
    return code == 0 and bool(out)


def follow_across_trees(source: Path, target: Path) -> bool:
    """Carry a tracked ledger file's move into both indexes when it changes CHECKOUT.

    Two worktrees of one clone have two indexes, so a move between them cannot be one
    rename however git is asked: the deletion belongs to the tree the file left and the
    addition to the tree it arrived in. Staging both is what keeps the move from reading
    as the loss it is not — the bare `D` with no counterpart that fifty stranded files
    taught this repository to recognise (#208).

    Same rule as `follow_in_git`: only for a file git ALREADY tracks. Where the founder
    does not commit the ledger, neither index is touched and nothing is granted (0008).
    """
    if not _tracked(source):
        return False
    staged = _git(["add", "-f", "--", str(target)], target.parent)[0] == 0
    return _git(["add", "-A", "--", str(source)], source.parent)[0] == 0 and staged


def stranded_deletions(root: Path, base: Path) -> list[Path]:
    """Ledger files git still has in its index and cannot find on disk.

    The trace a pre-1.61.1 transition left behind: the file was unlinked and rewritten one
    directory across, git was never told, and where an ignore rule hides the new copy the
    only visible half is the deletion. Absolute paths, so a caller standing anywhere can
    act on them.
    """
    code, out = _git(["ls-files", "--deleted", "--", str(base)], root)
    if code != 0 or not out:
        return []
    return [root / line for line in out.splitlines() if line.strip()]


def _move(task: Task, state: str, owner: str = "", branch: str = "",
          blocker: str | None = None) -> Task:
    """A state transition is a rename, in the working tree and in the index alike.

    Git records it as a rename, which merges cleanly — but only because `follow_in_git`
    puts it there. The move itself is plain filesystem work, so that a repository where
    git is unavailable or the file untracked still transitions.

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
        # The owner survives a CLOSURE, and only a closure. `done` used to clear it, so a
        # card carried no trace of who finished it — and the gates ask exactly that: a
        # session that added a card with the right paths, claimed it, did the work and
        # closed it was then told "nothing on the board says this session is working", with
        # `claim` answering "task is already done". The only way to keep the gate quiet was
        # to leave the card open until the session ended, which is the opposite of what
        # closing means (#220). `pause` and `next` still release it: those hand the work
        # back, and an unclaimed card is the point of them.
        owner or (meta.get("owner", "") if state in (DOING, DONE) else ""),
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
        opened_by=meta.get(OPENED_BY, ""),
    )
    store.atomic_write(target, updated, mode=0o644)
    if target != task.path:
        task.path.unlink(missing_ok=True)
        follow_in_git(task.path, target)
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
    # The owner under every id it has had. It claims in the main checkout and works in its
    # own tree, where every touch lands on the tree's id — read from the id the card names,
    # a session that never stopped looked idle from the moment it moved.
    records = [sessions.get(ctx, one) for one in sessions.identities(ctx, task.owner)]
    working = [record for record in records if record is not None]
    if not task.paths:
        return not all(sessions.is_idle(record) for record in working)
    return any(touched in task.paths for record in working for touched in record.last_touched)


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


def pause(ctx: GitContext, task_id: str, blocker: str,
          session_id: str = "") -> tuple[Task | None, str]:
    """Stop work and say what would restart it. `session_id` is who asks (see `_not_theirs`).

    The blocker is required. "Paused" without one is indistinguishable from abandoned, and
    the next session has no way to tell whether it is waiting on a decision, a credential,
    somebody else's merge, or nothing at all.
    """
    if len(blocker.strip()) < MIN_BLOCKER_CHARS:
        return None, (
            "a pause needs to say what would lift it — name the decision, the credential, "
            "the merge or the answer this is waiting on"
        )
    with store.file_lock(store.tier_b(ctx, CLAIM_LOCK)):
        task = find(ctx, task_id)
        if task is None:
            return None, f"no task {task_id}"
        if task.state == DONE:
            return None, f"task {task.id} is already done"
        refused = _not_theirs(ctx, task, session_id)
        if refused:
            return None, refused
        return _move_every(ctx, task_id, PAUSED, blocker=blocker.strip()) or task, ""


def _not_theirs(ctx: GitContext, task: Task, session_id: str) -> str:
    """Why the session asking may not pause or close `task`, or "" when it may.

    A live sibling's card is its own to hand back or to finish. `claude-bp-plan claim` refused
    one while `pause` and `done` took it, and the sibling's next write was then refused for
    having nothing on the board. Only a session this clone has registered is asked: the
    founder at a terminal, this plugin closing the cards a delivery carried, and a process
    that merely inherited somebody's session id are nobody's sibling here.
    """
    from . import sessions

    if not session_id or sessions.get(ctx, session_id) is None:
        return ""
    return _held_elsewhere(ctx, task, sessions.identities(ctx, session_id))


def resume(ctx: GitContext, task_id: str) -> tuple[Task | None, str]:
    """Return a paused task to the queue, clearing the blocker that held it."""
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    if task.state != PAUSED:
        return None, f"task {task.id} is not paused"
    return _move_every(ctx, task_id, NEXT, blocker="") or task, ""


def amend(ctx: GitContext, task_id: str, note: str = "", paths: list[str] | None = None,
          done_when: str = "", title: str = "") -> tuple[Task | None, str]:
    """Update what a task knows without changing which task it is.

    A task learns things while it waits — a file turns out to be the wrong one, a
    condition gets sharper. Without this the only ways to record that were to park a
    second task, which splits the identity, or to rewrite the file by hand, which the
    worktree gate refuses from the main checkout.
    """
    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    # Written into every copy for the same reason a transition moves every copy: a note
    # that reaches one worktree is a note the next reader does not get, and the copy it
    # did not reach is the one that survives `git worktree remove` (#210).
    amended: Task | None = None
    for copy in copies(ctx, task_id) or [task]:
        store.atomic_write(copy.path, _amended(copy, note, paths, done_when, title), mode=0o644)
        reloaded = _load(copy.path, copy.state)
        if reloaded is None:
            continue
        reloaded.worktree = copy.worktree
        if amended is None or (amended.worktree and not copy.worktree):
            amended = reloaded
    return amended, ""


def _amended(task: Task, note: str, paths: list[str] | None, done_when: str, title: str) -> str:
    """One task file rewritten with what it has just learned, and nothing else changed.

    Everything absent from the amendment is carried, `after` and `with` included — they
    were dropped by omission here, so a note on an ordered task silently cut it loose from
    the order it was written to respect.
    """
    meta, body = _frontmatter(task.path.read_text(encoding="utf-8"))
    return _render(
        task.id, title.strip() or meta.get("title", task.title), task.state, task.owner,
        task.branch,
        note.strip() or body,
        paths if paths is not None else task.paths,
        meta.get("source", ""),
        done_when.strip() or task.done_when,
        task.blocker,
        meta.get("created_at", ""),
        task.after,
        task.together,
        opened_by=meta.get(OPENED_BY, ""),
    )


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


# Held from the read to the rename by every transition that decides WHO holds a card —
# `claim`, `pause`, `done`. Unlocked, two sessions claiming one card both read it free, both
# printed "claimed", and the file named whichever wrote last; or one of them died on a file
# the other had already moved.
CLAIM_LOCK = "plan-claim.lock"

# How many times a claim reads a card that keeps moving under it. `resume`, the sweeps and
# the reaper move files without the lock, and a card that moved once has been re-read.
CLAIM_READS = 2


def _held_elsewhere(ctx: GitContext, task: Task, mine: set[str]) -> str:
    """Why a LIVE session other than this one holds `task`, or "" when none does.

    Liveness is checked rather than assumed: a claim held by a crashed session is taken
    over, which is the difference between a work ledger and a graveyard.
    """
    from . import sessions

    if not task.owner or task.owner in mine:
        return ""
    holder = sessions.get(ctx, task.owner)
    if holder is None or not sessions.is_live(ctx, holder):
        return ""
    return f"task {task.id} is held by live session {task.owner[:8]}"


def _claimable(ctx: GitContext, task_id: str, session_id: str) -> tuple[Task | None, str]:
    """The card as it stands now, when this session may take it. (None, why not) otherwise."""
    from . import sessions

    task = find(ctx, task_id)
    if task is None:
        return None, f"no task {task_id}"
    if task.state == DONE:
        return None, f"task {task.id} is already done"
    mine = sessions.identities(ctx, session_id)
    # This session's already, under the id it had where it claimed it: in the main
    # checkout, before it entered its own tree. Handed to the id it has now, never refused
    # as a live sibling's — that sibling is itself, and the only other way out was filing
    # the same work twice (#131). Its plan was demanded when it was first claimed.
    if task.owner in mine:
        return task, ""
    # The plan, demanded where the plan has to exist. `pre-tool` already refuses a write
    # that no claimed card covers, so requiring it HERE is what makes "no code without a
    # plan" binding — and it costs the founder nothing, where the harness's own plan mode
    # ends in an approval dialog they spent several releases removing.
    #
    # At `claim` and not at `add`: filing a rough card has to stay a single line, or the
    # board stops being written to. Starting one is the moment the plan is owed.
    refused = _held_elsewhere(ctx, task, mine) or _unplanned(task)
    return (None, refused) if refused else (task, "")


def claim(ctx: GitContext, task_id: str, session_id: str, branch: str) -> tuple[Task | None, str]:
    """Take ownership. Returns (task, error). A task owned by a LIVE session is refused.

    Read, judged and moved under one lock (`CLAIM_LOCK`), and read again if the card moved
    anyway — a transition that takes no lock can still move the file between the read and
    the rename, and a claim that dies on a traceback has told nobody anything.
    """
    with store.file_lock(store.tier_b(ctx, CLAIM_LOCK)):
        for _ in range(CLAIM_READS):
            task, error = _claimable(ctx, task_id, session_id)
            if task is None:
                return None, error
            try:
                return _move(task, DOING, owner=session_id, branch=branch), ""
            except FileNotFoundError:
                continue
    return None, f"task {task_id} kept moving while it was being claimed — claim it again"


def complete(ctx: GitContext, task_id: str, session_id: str = "") -> tuple[Task | None, str]:
    """Close a card. The finish condition is demanded at `claim`, not here.

    v1.26.0 demanded it at this end, which was the right rule at the wrong moment: a card
    that reaches `doing` has been through `claim`, so by the time anything is closed the
    condition already exists, and the check here could only ever fire for a file edited by
    hand. Asked where the plan is owed instead — before the work, not after it.

    `session_id` is who asks, and a live sibling's card is not theirs to close
    (`_not_theirs`).
    """
    with store.file_lock(store.tier_b(ctx, CLAIM_LOCK)):
        task = find(ctx, task_id)
        if task is None:
            return None, f"no task {task_id}"
        refused = _not_theirs(ctx, task, session_id)
        if refused:
            return None, refused
        # Every copy, not the one this directory happens to hold: see `_move_every`.
        landed = _move_every(ctx, task_id, DONE) or _move(task, DONE)

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


# The ledger had no closing half. `complete` had exactly one caller — the CLI — so a card
# reached `doing` because a gate demanded it and left `doing` only if somebody remembered
# to type the command. Nothing in eight releases ever did. Measured in this repository:
# card 0050 sat in `doing` for four days, across every session, over work that had been
# merged and tagged on the first of them.
#
# That is not untidiness. `doing` is the row every sibling reads to decide what is safe to
# touch, and a card whose work shipped is the board asserting a collision that cannot
# happen — the same lie the reaper and `sweep_idle` exist to stop telling from the other
# end. And the founder's own account of it is the sharper one: they had given the word to
# merge, and were then asked a second question about closing the cards, or waited for a
# command, or got nothing at all.
#
# So the delivery closes them, and the plugin does it rather than asking. Decision 0010
# already settles the authority: `+merge` is the founder's word on the work and the
# session does the rest without asking again. The card that claimed that work is the rest.


def carried_by(task: Task, delivered: list[str]) -> list[str]:
    """The files this card named that the delivery actually carried.

    A card cannot reach `doing` without naming its files — `claim` refuses one that does
    not — so every in-flight card has something to compare a delivery against. That makes
    "did this finish that card" a question with a mechanical answer instead of a judgement
    the model would have to be trusted for, which is decision 0002 applied to the closing
    end of the ledger rather than the opening one.

    ANY of the files rather than all of them. A card names the code it changes and the test
    that proves it, and half a dozen more when the work is a subsystem; a delivery that
    carried the change and not the fixture is still the delivery of that work. Demanding
    the whole list closes almost nothing, which is the failure this exists to end. A card
    entry that names a directory matches everything under it, because that is what gets
    written when the work is a subsystem rather than a file.
    """
    if not task.paths or not delivered:
        return []
    carried = []
    for named in task.paths:
        stem = named.rstrip("/")
        if any(rel == stem or rel.startswith(stem + "/") for rel in delivered):
            carried.append(named)
    return carried


def settle_delivered(ctx: GitContext, session_id: str, delivered: list[str],
                     what: str) -> list[Task]:
    """Close this session's cards whose files the delivery carried. Returns what closed.

    Owned by THIS session only, under any id it has had (`held_by`). A sibling's card over
    the same files is its own to close: it may be mid-change on top of what just landed,
    and taking its row off the board is the same lie in the other direction.

    What closed it is written into the card before it moves, because a closure nobody can
    account for is worse than a card left open — the founder reads outcomes, and "who
    decided this was finished" has to be answerable from the file itself.
    """
    if not delivered:
        return []
    closed: list[Task] = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for task in held_by(ctx, session_id):
        carried = carried_by(task, delivered)
        if not carried:
            continue
        note = f"[{stamp}] closed on delivery — {what} carried {', '.join(carried[:6])}."
        task.body = f"{task.body}\n\n{note}".strip() if task.body else note
        _rewrite_body(task)
        landed, _ = complete(ctx, task.id)
        if landed:
            closed.append(landed)
    return closed


def held_by(ctx: GitContext, session_id: str) -> list[Task]:
    """The cards this session is holding in `doing`, under any id it has had.

    A session that claims its card in the main checkout and then enters its own tree is a
    second identity there (`sessions.identities`), and the card is still its own. Asked by
    id alone, the gates told it nothing on the board said it was working, and the remedy
    they named — claim it — was refused as a live session's, which was itself.
    """
    return _owned(ctx, session_id, DOING)


def closed_by(ctx: GitContext, session_id: str) -> list[Task]:
    """The cards this session has closed, under any id it has had. `done` keeps the owner
    precisely so this can be asked (#220)."""
    return _owned(ctx, session_id, DONE)


def _owned(ctx: GitContext, session_id: str, state: str) -> list[Task]:
    """The cards in `state` whose owner is this session by any of its ids."""
    from . import sessions

    owners = sessions.identities(ctx, session_id)
    return [task for task in load_all(ctx, state) if task.owner in owners]


def closure_demand(tasks: list[Task]) -> str:
    """Why a finish over delivered work is refused while its cards are still open.

    The backstop for every delivery the plugin did not itself perform: a merge on the
    website, a branch fast-forwarded by hand, a push straight to a trunk. `settle_delivered`
    closes what it can see; this catches the rest, and it is a refusal rather than a note
    because a note is the thing that was already being forgotten.

    `pause` is offered beside `done` and is not a formality. Work can reach the base branch
    and still not be finished — a feature merged behind a flag, a migration merged and not
    yet run — and a gate whose only exit is "declare it done" buys a clean board by making
    the ledger lie.
    """
    listed = "\n".join(f"  - {t.id}  {t.title[:80]}" for t in tasks[:6])
    more = f"\n  ... and {len(tasks) - 6} more" if len(tasks) > 6 else ""
    return (
        "This session's work has reached the base branch and the board still says it is in "
        f"flight:\n{listed}{more}\n"
        "  Every sibling reads `doing` to decide what is safe to touch, so a card over "
        "shipped work is the board claiming a collision that cannot happen.\n"
        f"  claude-bp-plan done {tasks[0].id}\n"
        f'  or, if it shipped without being finished: claude-bp-plan pause {tasks[0].id} '
        '--blocker "<what is still owed>"\n'
        "  Do not ask the founder which — they accepted the work when they accepted the "
        "merge, and this is the half that follows from it."
    )


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
        opened_by=meta.get(OPENED_BY, ""),
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
