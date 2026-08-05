"""What an upgrade owes a repository that already has state in it.

A plugin that keeps growing keeps changing the shape of what it wrote last month. Three
things follow, and only the first is usually handled.

**Old state must keep loading.** A field added today is absent in every record written
before it, and "absent" has to read as a default rather than as a parse failure. That is
cheap and it is done at the readers.

**Broken state must be repaired, not stepped around.** A truncated file, a record from a
version with a bug in its writer, a task file that lost its frontmatter — these survive
upgrades indefinitely, because nothing goes looking for them. They are found here.

**A workaround the plugin caused must be taken over when the plugin grows the feature.**
This is the one nobody does. Before the ledger could park a task, the honest thing for a
session to do was write `docs/scoring/TODO-dictionary-realign.md` — and once parking
exists, that file is a second task system in a repository that now has a first one. Two
systems is worse than either, because neither is trusted and both are half-read.

Every step is idempotent and recorded, so an upgrade that runs twice does nothing the
second time, and a step added later runs once on a repository that has been installed for
months. Repairs run themselves. **Adoption does not** — it rewrites files in the
founder's repository, and a plugin that edits `docs/` on its own initiative during an
upgrade is a plugin nobody installs twice. It is detected, surfaced, and applied by one
command.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import store
from .gitctx import GitContext

# Tier B, not Tier A, and decision 0001 is why: this is bookkeeping about what has been
# done to THIS clone, not a fact about the repository worth committing. Written to the
# working tree it dirtied `git status` on every session start — which an existing test
# caught, because a previous version of this plugin did exactly that with the stage marker.
#
# Per-clone means a fresh clone runs the repairs again, and that is correct rather than
# wasteful: every repair checks before it writes, so on already-repaired state they are
# no-ops, and a clone that genuinely needs them gets them.
LEDGER = "migrations.json"

# `TODO-<something>.md` is a task somebody parked by hand. A bare `TODO.md` is usually a
# curated document a project maintains on purpose, and adopting one would be taking over
# something that was never a workaround. The hyphen is the whole distinction, and it is
# the difference between helping and helping yourself to someone's documentation.
_PARKED_BY_HAND = re.compile(r"(?:^|/)TODO[-_][\w.-]+\.md$", re.I)

# The sentence left where an adopted file stood. Adoption has to recognise its own work:
# without this, a second run adopts the pointer, files a task whose body is the pointer
# text, and leaves a fresh pointer to adopt on the third — one task per invocation,
# forever. Found by running it twice.
POINTER = "Moved into the work ledger as task"

# Directories nobody parks a task in, and which are expensive to walk.
_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".claude"}


def _done(ctx: GitContext) -> dict:
    record = store.read_json(store.tier_b(ctx, LEDGER), default={})
    return record if isinstance(record, dict) else {}


def _mark(ctx: GitContext, step: str, detail: str) -> None:
    record = _done(ctx)
    record[step] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "detail": detail}
    store.write_json(store.tier_b(ctx, LEDGER), record)


def pending(ctx: GitContext) -> list[str]:
    return [name for name in _REPAIRS if name not in _done(ctx)]


def repair(ctx: GitContext) -> list[str]:
    """Run every repair this repository has not had. Returns what was actually changed.

    Never raises. An upgrade that dies halfway through fixing something has left the
    repository worse than the defect it came to fix, and the founder with no way to tell
    which half ran.
    """
    changed: list[str] = []
    for name, step in _REPAIRS.items():
        if name in _done(ctx):
            continue
        try:
            detail = step(ctx)
        except Exception:  # noqa: BLE001 - a failed repair must not brick a session
            continue
        _mark(ctx, name, detail)
        if detail:
            changed.append(f"{name}: {detail}")
    return changed


def _backfill_task_paths(ctx: GitContext) -> str:
    """Task files written before tasks carried the files they were about.

    Readers already default the field to empty, so nothing is broken — but a task with no
    `paths:` line cannot be told apart from one where the field was deliberately left
    empty, and the parking gate refuses the second. Writing the empty field makes the
    distinction real.
    """
    from . import plan

    touched = 0
    for task in plan.load_all(ctx):
        try:
            text = task.path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---") or "\npaths:" in text:
            continue
        head, sep, rest = text.partition("\n---")
        if not sep:
            continue
        store.atomic_write(task.path, f"{head}\npaths: {sep}{rest}", mode=0o644)
        touched += 1
    return f"{touched} task file(s) given a paths field" if touched else ""


def _quarantine_unreadable_state(ctx: GitContext) -> str:
    """Committed state that no longer parses, moved aside instead of read past forever.

    A half-written JSON file under Tier A survives every upgrade: each reader catches its
    own decode error and carries on with a default, so nothing is broken loudly and
    nothing is ever fixed. Moved to `.broken` with the original kept, because deleting a
    founder's file to fix a parse error is not a trade this plugin gets to make.
    """
    root = store.tier_a(ctx)
    moved = 0
    for path in sorted(root.glob("*.json")):
        raw = store.read_json(path, default=None)
        if raw is not None:
            continue
        try:
            path.replace(path.with_suffix(".json.broken"))
        except OSError:
            continue
        moved += 1
    return f"{moved} unreadable state file(s) set aside as .broken" if moved else ""


_REPAIRS = {
    "0001-task-paths": _backfill_task_paths,
    "0002-quarantine-unreadable": _quarantine_unreadable_state,
}


# Checkbox items, in every shape markdown writes them: `-`, `*`, `+`, or `1.` before the
# box. Filename patterns were the first version of this and they missed an entire real
# setup — `docs/TODO.md`, `docs/pre-release-todo.md`, `.claude/commands/todo.md` — because
# nobody agreed to the naming convention the plugin was quietly expecting. What a
# registry looks like INSIDE is not a convention; it is markdown.
_OPEN_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[ \]\s+(?P<text>\S.*?)\s*$", re.M)
_DONE_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]\s", re.M)

IGNORED = "adoption-ignored.json"

# Below this a document is prose that happens to contain a checkbox, not a registry.
MIN_ITEMS = 2


def open_items(text: str) -> list[str]:
    """The unfinished items a document is tracking."""
    return [m.group("text")[:plan_title_limit()] for m in _OPEN_ITEM.finditer(text)]


def _ignored(ctx: GitContext) -> dict:
    record = store.read_json(store.tier_a(ctx, IGNORED), default={})
    return record if isinstance(record, dict) else {}


def ignore(ctx: GitContext, relative: str, why: str = "curated by hand") -> None:
    """Declare a document none of the plugin's business, permanently.

    Without this the board nags about the same file every session forever, and a warning
    nothing can clear is one the founder learns to scroll past — which costs the warnings
    that matter. Tier A, because "this registry is ours, leave it alone" is a fact about
    the repository and should travel with it rather than be re-decided per clone.
    """
    record = _ignored(ctx)
    record[relative] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "why": why}
    store.write_json(store.tier_a(ctx, IGNORED), record, mode=0o644)


def registries(ctx: GitContext) -> list[Path]:
    """Documents that are tracking work, found by what is in them.

    Everything the founder has not already said to leave alone, and not the ledger's own
    files. `.claude/` is skipped because a slash-command that happens to describe a TODO
    workflow is not a backlog.
    """
    root = ctx.worktree_root
    skip = _ignored(ctx)
    found: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if any(part in _SKIP for part in path.relative_to(root).parts) or relative in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if POINTER in text or len(open_items(text)) < MIN_ITEMS:
            continue
        found.append(path)
    return found


def coverage(ctx: GitContext, path: Path) -> tuple[int, int]:
    """(items in the document, items already in the ledger from it).

    This is the whole reason migration can be delegated to the agent rather than asked
    of it. The plugin does not have to read the prose or judge the result — it counts
    what the document is tracking and counts what the ledger holds from that document,
    and the gap is a number. An agent that says it migrated a registry and left twenty
    items behind is contradicted by arithmetic, not by opinion.
    """
    from . import plan

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, 0
    relative = path.relative_to(ctx.worktree_root).as_posix()
    migrated = sum(1 for task in plan.load_all(ctx) if task.source == relative)
    return len(open_items(text)), migrated


def brief(ctx: GitContext, path: Path) -> str:
    """The instruction handed to the session, and the check that closes it.

    Filename patterns cannot tell a curated registry from a session's scratch note, and
    prose cannot be parsed into a handoff by regular expressions — but a model reading the
    document can do both, and the arithmetic above says whether it did. So this is not the
    plugin asking nicely; it is the plugin delegating a mechanical job it cannot do and
    keeping the verification for itself.
    """
    relative = path.relative_to(ctx.worktree_root).as_posix()
    total, migrated = coverage(ctx, path)
    items = open_items(path.read_text(encoding="utf-8", errors="replace"))
    listed = "\n".join(f"  - {item}" for item in items[:12])
    more = f"\n  … and {len(items) - 12} more" if len(items) > 12 else ""
    return "\n".join([
        f"{relative} tracks {total} open item(s); {migrated} of them are in the ledger.",
        "",
        listed + more,
        "",
        "For each item NOT yet in the ledger, read enough of the repository to fill in a",
        "real handoff, then run:",
        "",
        f'  claude-bp-plan park "<title>" --paths <files> --note "<what is known, what was',
        f'    ruled out, where it stands>" --source {relative}',
        "",
        "`park` refuses a title with no files and no substance, so a thin one will not land.",
        f"Run `claude-bp-plan adopt --check {relative}` when you are done: it counts what is",
        "left rather than taking your word for it.",
        "",
        "If this document is curated by hand and should stay the source of truth, say so",
        f"once and it stops being raised: claude-bp-plan adopt --ignore {relative}",
    ])


def parked_by_hand(ctx: GitContext) -> list[Path]:
    """TODO files a session wrote because the ledger could not park a task yet."""
    found: list[Path] = []
    root = ctx.worktree_root
    for path in root.rglob("TODO*.md"):
        if any(part in _SKIP for part in path.relative_to(root).parts):
            continue
        if not _PARKED_BY_HAND.search(path.relative_to(root).as_posix()):
            continue
        try:
            if POINTER in path.read_text(encoding="utf-8", errors="replace"):
                continue
        except OSError:
            continue
        found.append(path)
    return sorted(found)


def adopt(ctx: GitContext, path: Path) -> str:
    """Move one hand-written TODO into the ledger, leaving a pointer where it stood.

    The original is rewritten rather than deleted. Anything that linked to it still
    resolves, git keeps the whole text, and there is exactly one place the task now lives
    — which is the entire point of adopting it at all.
    """
    from . import plan

    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ctx.worktree_root).as_posix()
    title = _title_of(text) or path.stem.replace("-", " ").replace("_", " ")
    task = plan.park(
        ctx,
        title=title,
        body=f"Adopted from `{relative}`.\n\n{text.strip()}",
        branch=ctx.branch,
        paths=_paths_in(text, ctx.worktree_root) or [relative],
    )
    store.atomic_write(
        path,
        f"# {title}\n\n{POINTER} {task.id}.\n\n"
        f"    claude-bp-plan show {task.id}\n\n"
        "Kept as a pointer so links to this path still resolve; the text is in the task "
        "and in git history.\n",
        mode=0o644,
    )
    return task.id


def _title_of(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:plan_title_limit()]
    return ""


def plan_title_limit() -> int:
    from . import plan

    return plan.MAX_TITLE_CHARS


def _paths_in(text: str, root: Path) -> list[str]:
    """Repository files the note mentions — the ones the next session has to open.

    Only tokens that exist. A hand-written TODO is prose, and prose is full of things that
    look like filenames; keeping the ones that resolve is what makes the difference
    between a file list and a guess.
    """
    found: list[str] = []
    for token in re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}", text):
        candidate = token.strip(".,;:()[]`'\"")
        if candidate and (root / candidate).exists() and candidate not in found:
            found.append(candidate)
    return found[:12]


def line(ctx: GitContext) -> str:
    """One line when work is still being tracked outside the ledger.

    Counts ITEMS, not files. "2 documents" says nothing about how much is at stake;
    "31 open items in 2 documents" is the number that decides whether it is worth a turn.
    """
    parts: list[str] = []

    # Scratch notes a session wrote because the ledger could not park a task. These carry
    # no checkbox list — they are prose — so counting items says nothing about them, and
    # an earlier version of this line dropped them entirely. Caught by its own test.
    scratch = parked_by_hand(ctx)
    if scratch:
        parts.append(f"{len(scratch)} scratch TODO file(s)")

    left = 0
    documents = 0
    for path in registries(ctx):
        total, migrated = coverage(ctx, path)
        if total > migrated:
            left += total - migrated
            documents += 1
    if left:
        parts.append(f"{left} open item(s) in {documents} document(s)")

    if not parts:
        return ""
    return (
        " and ".join(parts) + " tracked outside the work ledger — "
        "`claude-bp-plan adopt` for what to do about it"
    )
