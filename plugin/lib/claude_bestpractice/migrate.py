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
import subprocess
import time
from pathlib import Path, PurePosixPath

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


def _mark(ctx: GitContext, step: str, revision: int, detail: str) -> None:
    record = _done(ctx)
    record[step] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "revision": revision,
        "detail": detail,
    }
    store.write_json(store.tier_b(ctx, LEDGER), record)


def _ran_at(ctx: GitContext, step: str) -> int:
    """Which revision of this repair the clone has had, or -1 for none at all.

    A record written before repairs carried revisions reads as 0, so every repair at
    revision 1 or above runs again on it. That is the point rather than a side effect:
    such a clone was last reconciled by code that has since changed.
    """
    entry = _done(ctx).get(step)
    if entry is None:
        return -1
    return int(entry.get("revision") or 0) if isinstance(entry, dict) else 0


def pending(ctx: GitContext) -> list[str]:
    return [name for name, (revision, _) in _REPAIRS.items() if _ran_at(ctx, name) < revision]


def repair(ctx: GitContext) -> list[str]:
    """Reconcile this repository with what the installed version now knows. Returns what
    actually changed.

    Keyed by REVISION and not by name, which is the difference between an upgrade and a
    checklist. A repair whose implementation gets better — a case it missed, a shape it
    could not read, a file it should not have touched — has already been recorded as done
    in every repository that ran the old one, and under a name-keyed ledger those are
    exactly the repositories that never get the improvement. The founder upgrades on top
    of what was working, several versions at a time, so "already ran once" is the wrong
    question; "ran under which code" is the right one.

    Bump the revision beside a repair whenever what it does changes. Every step is still
    idempotent, so re-running one that has nothing left to fix costs a walk and writes
    nothing.

    Never raises. An upgrade that dies halfway through fixing something has left the
    repository worse than the defect it came to fix, and the founder with no way to tell
    which half ran.
    """
    changed: list[str] = []
    for name, (revision, step) in _REPAIRS.items():
        if _ran_at(ctx, name) >= revision:
            continue
        try:
            detail = step(ctx)
        except Exception:  # noqa: BLE001 - a failed repair must not brick a session
            continue
        _mark(ctx, name, revision, detail)
        if detail:
            changed.append(f"{name}: {detail}")
    return changed


def repaired_line(changed: list[str]) -> str:
    """What the upgrade actually changed in this repository, said once.

    Empty on every session that found nothing to fix, which is all of them after the
    first — a repair that writes to the founder's tree and says nothing is indistinguishable
    from a session that decided to edit their files on a whim.
    """
    if not changed:
        return ""
    shown = "; ".join(changed[:3])
    more = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
    return f"\nupgrade repaired this repository: {shown}{more}"


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


def _absorb_scratch_todos(ctx: GitContext) -> str:
    """Pull the scratch TODO files the plugin's own absence caused into the ledger.

    An upgrade has to fix the repository it lands in, not only behave better on the next
    one. A founder updates the plugin on top of what was working, so a workaround written
    before the ledger could park a task stays a second source of truth forever unless the
    upgrade absorbs it — and the sessions that read one never see the other.

    Only the mechanical half. `parked_by_hand` finds files a SESSION wrote as a stand-in,
    and importing one needs no judgement: the original is rewritten to a pointer, so
    nothing that linked to it breaks and git keeps the whole text. Prose registries the
    founder curates are still only reported, because deciding what in them is a task is
    the agent's job and not a regex's.
    """
    adopted = []
    for path in parked_by_hand(ctx):
        try:
            task_id = adopt(ctx, path)
        except OSError:
            continue
        adopted.append(f"{path.relative_to(ctx.worktree_root).as_posix()} -> {task_id}")
    return "; ".join(adopted)



def _our_trees(records: list) -> dict:
    """The trees this plugin provisioned, keyed by where they are now."""
    found = {}
    for record in records:
        body = store.read_json(record, default={}) or {}
        current = Path(str(body.get("path") or ""))
        if body.get("provisioned_by_plugin") and current.is_dir():
            found[current.resolve()] = (record, body)
    return found


def _repoint(ours: dict, was: Path, now: Path) -> None:
    """Follow a moved tree in our registry, or the next refusal names a path that is gone.

    Silent for a tree we never recorded — the founder's own, which is now moved too and
    has nothing here to update.
    """
    record, body = ours.get(was.resolve(), (None, None))
    if record is None:
        return
    body["path"] = str(now)
    store.write_json(record, body)


def _trees_that_still_prompt(ctx: GitContext, home: Path) -> list[Path]:
    """Every worktree entering still asks about, ours and the founder's alike.

    Going by our own records alone was the whole defect: a tree the founder made by hand,
    or one the CLI's `--worktree` flag made, has no record here — so the repair could not
    see it and never would, and entering it asked for authorisation on every session,
    forever. Reported three times before the cause was looked for in this function rather
    than in the CLI's changelog.

    Git is the register that knows about all of them.

    Skipped: a tree already under `home` is where it should be; the tree THIS session is
    standing in cannot be moved out from under itself; and a tree a live session records
    as its own belongs to work in progress — moving a directory out from under a running
    session breaks it.

    The main checkout is skipped too, and that line is belt over braces rather than a
    rule: `git worktree move` refuses the main working tree on its own, so no test can
    tell the check from its absence. Said plainly rather than dressed up, and kept because
    it saves a pointless subprocess — the same honest claim the sibling guard below makes.
    """
    from .gitctx import worktree_paths

    try:
        registered = worktree_paths(ctx)
    except Exception:  # noqa: BLE001 - a repair must never be what breaks a session start
        return []
    keep = _trees_to_leave_alone(ctx)
    if keep is None:
        return []
    return [p for p in registered if p not in keep and _still_prompts(p, home)]


def _trees_to_leave_alone(ctx: GitContext) -> set[Path] | None:
    """Paths moving would break: the main checkout, this session's tree, and any tree a
    live session records as its own.

    `None` when the live sessions cannot be read — which is not the same as "none are
    live" and must not collapse into it. Unknown means move nothing: the alternative is
    relocating a directory out from under a session that is working in it, and a repair
    that does that is worse than the prompt it came to remove.
    """
    from . import sessions, worktree

    keep = {worktree.main_checkout(ctx).resolve(), ctx.worktree_root.resolve()}
    try:
        return keep | {Path(rec.worktree).resolve() for rec in sessions.live_sessions(ctx)}
    except Exception:  # noqa: BLE001 - a repair must never be what breaks a session start
        return None


def _still_prompts(path: Path, home: Path) -> bool:
    """A directory that exists and is not already in the no-prompt zone."""
    try:
        return path.is_dir() and home.resolve() not in path.parents
    except OSError:
        return False


def _move_trees_into_the_no_prompt_zone(ctx: GitContext) -> str:
    """Trees this plugin made beside the repository, moved to where entering never asks.

    `EnterWorktree` prompts for approval on any path outside `.claude/worktrees/`,
    unconditionally, before permissions are consulted — so every tree provisioned before
    v1.14.0 asks the founder for authorisation on every entry, forever. Changing where new
    ones are made left the existing eight exactly as they were.

    `git worktree move` and not a delete-and-recreate: it carries the branch and the
    uncommitted work with it, verified against a dirty tree. Without `--force`, so a locked
    tree or one with submodules is left alone rather than broken.
    """
    from . import worktree

    home = worktree.home_of(ctx)
    moved = []
    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return ""
    by_path = _our_trees(records)
    theirs = [p for p in _trees_that_still_prompt(ctx, home) if p.resolve() not in by_path]

    for current in [p for p in _trees_that_still_prompt(ctx, home) if p.resolve() in by_path]:
        # One guard, covering both cases: a tree already under the home resolves to itself,
        # and a sibling whose name is taken is left where it is rather than colliding.
        # Belt over braces, and said so rather than dressed up as a rule — `git worktree
        # move` refuses a self-move on its own, so no test can tell this line from its
        # absence. It saves a pointless subprocess, which is the honest claim for it. The
        # safety here is git's refusal, exactly as in the reaper.
        target = home / current.name
        if target.exists():
            continue
        worktree.hide(ctx)
        home.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["git", "worktree", "move", str(current), str(target)],
            cwd=str(ctx.worktree_root), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=120,
        )
        if done.returncode != 0:
            continue
        _repoint(by_path, current, target)
        moved.append(target.name)
    return _worktree_repair_note(moved, theirs)


def _worktree_repair_note(moved: list, theirs: list) -> str:
    """What the repair did, and what it deliberately only named.

    Named and not moved: going by our own records alone is what hid these for three
    reports — but a tree this plugin did not make may have an editor or a shell sitting in
    it, and `git worktree move` under a running process breaks it. Our own trees are
    different: the registry says who is in them.
    """
    said = []
    if moved:
        said.append(f"{len(moved)} worktree(s) moved under .claude/worktrees/, where "
                    "entering them no longer asks for approval")
    if theirs:
        shown = ", ".join(str(p) for p in theirs[:3])
        more = f" (+{len(theirs) - 3} more)" if len(theirs) > 3 else ""
        said.append(
            f"{len(theirs)} worktree(s) this plugin did not make sit outside "
            f".claude/worktrees/, so entering them asks for approval every time: {shown}"
            f"{more}. Move one with `git worktree move <path> "
            "<repo>/.claude/worktrees/<name>`, or allow `EnterWorktree` once in "
            "~/.claude/settings.json"
        )
    return "; ".join(said)


def _lift_the_tool_call_ceiling(ctx: GitContext) -> str:
    """The ceiling this plugin invented, taken back out of repositories that kept it.

    `max_tool_calls` defaulted to 2000 and `config.save` writes every key, so the number is
    on disk in every repository that ever saved a config — and a fix that only changes the
    default leaves all of them blocked. The founder upgrades on top of what was working.

    Only the value this plugin chose. A number the founder set themselves is their word on
    the subject and is left exactly as it is.
    """
    from . import config

    path = store.tier_a(ctx, config.CONFIG_NAME)
    raw = store.read_json(path, default=None)
    if not isinstance(raw, dict) or raw.get("max_tool_calls") != 2000:
        return ""
    raw["max_tool_calls"] = 0
    store.write_json(path, raw, mode=0o644)
    return "the 2000-call ceiling this plugin set is off; set it yourself to bring it back"


def _drop_the_witness_timeout(ctx: GitContext) -> str:
    """The second ceiling this plugin invented, taken back out the same way.

    v1.37.0 made the witness timeout configurable, which read as a fix and was not: the
    300 seconds it lifted sat inside a 900-second Stop hook budget, so a repository that
    raised it only moved the death of its run from our timeout to the harness's, where
    there is no message at all. The setting could not deliver what it promised.

    `config.save` writes every key, so the number is on disk in every repository that
    saved a config while it existed — and leaving it there leaves a knob that does
    nothing, which is worse than no knob. The ceiling comes from the hook budget now.
    """
    from . import config

    path = store.tier_a(ctx, config.CONFIG_NAME)
    raw = store.read_json(path, default=None)
    # The key check is belt over braces and said so rather than dressed up as a rule:
    # `repair` swallows what a step raises, so without it a config lacking the key would
    # fail on `pop` and change nothing either way. No mutation can tell this line from its
    # absence, which is why the test asserting it was deleted rather than kept.
    if not isinstance(raw, dict) or "witness_timeout_seconds" not in raw:
        return ""
    raw.pop("witness_timeout_seconds")
    store.write_json(path, raw, mode=0o644)
    return ("`witness_timeout_seconds` is gone — it could not grant time the Stop hook "
            "does not have; the ceiling now comes from the hook's own budget")


def _forget_a_statement_that_was_only_a_switch(ctx: GitContext) -> str:
    """The founder's word, taken by the wrong reader and kept as what the session is for.

    A line like `worktree_setup ['bash', 'infra/scripts/worktree_db_init.sh']` is a key
    and a value. Until this release it also cleared every test for a statement of work —
    it is long, and it names a path — so it became the session's task, and stayed there:
    on the board, in the branch name, and quoted back by every scope-drift refusal (#166).

    The fix stops it happening. This takes it out of the sessions it already happened to,
    because the founder upgrades on top of what was working and a statement is only
    replaced when they say something new.
    """
    from . import config, sessions

    cleared = 0
    for rec in sessions.load_all(ctx):
        if rec.task_statement and config.is_only_a_switch(rec.task_statement):
            sessions.touch(ctx, rec.session_id, task_statement="")
            cleared += 1
    if not cleared:
        return ""
    return (f"{cleared} session(s) had a config line as their task statement; cleared, and "
            "the next thing the founder says will fill it")


def _collapse_the_decision_inbox(ctx: GitContext) -> str:
    """One row per draft, in a store that had fifteen copies of some of them.

    `record` appended unconditionally while the extractor re-read the same recent turns
    every time it ran, so the inbox reached sixty rows carrying four distinct sentences —
    and `claude-bp status` pointed at it as the next action. Nobody reviews a list that
    deep, and a list nobody can act on drifts without limit.

    Rewritten rather than left to `pending`, which collapses on read: the file itself was
    89KB and still growing on every run.
    """
    from . import drafts

    path = store.tier_b(ctx, drafts.INBOX_FILE)
    rows = [row for row in store.read_jsonl(path) if isinstance(row, dict)]
    if not rows:
        return ""
    keep = drafts.pending(ctx)
    resolved = [row for row in rows if row.get("resolved")]
    if len(keep) + len(resolved) >= len(rows):
        return ""
    dropped = len(rows) - len(keep) - len(resolved)
    store.rewrite_jsonl(path, resolved + keep)
    return (f"{dropped} duplicate decision draft(s) collapsed; the inbox now holds "
            f"{len(keep)} waiting to be reviewed")


def _drop_defects_from_things_that_are_not_gates(ctx: GitContext) -> str:
    """Crashes captured from programs this plugin does not ship.

    `guard` is reached by importing the library, so anything that imports it and raises
    was recorded — ninety-four rows of this plugin's own test fixture, `RuntimeError:
    kaboom`, sitting behind a command whose whole job is to file them at GitHub.
    """
    from . import defects

    path = store.tier_b(ctx, defects.DEFECTS_FILE)
    rows = [row for row in store.read_jsonl(path) if isinstance(row, dict)]
    if not rows:
        return ""
    try:
        ours = {entry.name for entry in (Path(__file__).resolve().parents[2] / "bin").iterdir()}
    except OSError:
        return ""
    keep = [row for row in rows if str(row.get("gate", "")) in ours]
    if len(keep) == len(rows):
        return ""
    dropped = len(rows) - len(keep)
    store.rewrite_jsonl(path, keep)
    return (f"{dropped} captured crash(es) came from something this plugin does not ship; "
            "dropped, because `claude-bp-report send` would have filed them")


# name -> (revision, step). Raise the revision when the step's behaviour changes; every
# clone that ran an older revision reconciles again on its next session start.
def _shrink_unverified_reasons(ctx: GitContext) -> str:
    """Failure markers written when a gate's reason had no length limit.

    `unverified.jsonl` is append-only and clone-wide, and every pull-request readiness
    check parses the whole of it. Until 1.54.0 the row carried the gate's reason verbatim,
    and the reason carried the end of the suite output — bounded at 25 LINES and nothing
    else. One run that failed with a base64 blob, a wide assert diff or a minified bundle
    on a single line wrote that line here, permanently, to be re-parsed on every check
    afterwards.

    Only the field shrinks. Which branch finished unverified is the only thing any reader
    looks at, and dropping the row would forgive a finish nobody proved.
    """
    path = store.tier_b(ctx, "unverified.jsonl")
    rows = store.read_jsonl(path)
    if not rows:
        return ""
    cap = 2000
    shrunk = 0
    out = []
    for row in rows:
        if isinstance(row, dict) and len(str(row.get("reason") or "")) > cap:
            row = {**row, "reason": str(row["reason"])[:cap]}
            shrunk += 1
        out.append(row)
    if not shrunk:
        return ""
    store.rewrite_jsonl(path, out)
    return f"{shrunk} oversized unverified-finish record(s) trimmed"


def _close_cards_whose_work_shipped(ctx: GitContext) -> str:
    """Cards left in flight by a ledger that had no closing half.

    Until 1.56.0 nothing anywhere closed a card: `plan.complete` had one caller, the CLI,
    so a card reached `doing` because a gate demanded it and left only if somebody
    remembered to type the command. Every repository that has been running this plugin
    therefore carries rows saying work is in flight over work that shipped weeks ago —
    this one carried card 0050 for four days, over a release it had merged and tagged on
    the first of them. The new rule closes them going forward and reaches nothing already
    on the board, which is the half an upgrade owes.

    Two conditions, both conservative, because this runs unattended in somebody's
    repository. The owner must not be a live session — a card a chat is holding right now
    is that chat's to close. And EVERY file the card named must be byte-identical to what
    the trunk holds, where `settle_delivered` needs only one: it is judging a delivery it
    watched happen, and this is inferring one from the state left behind.
    """
    from . import evidence, plan, sessions

    closed = 0
    for task in plan.load_all(ctx, plan.DOING):
        holder = sessions.get(ctx, task.owner) if task.owner else None
        if holder is not None and sessions.is_live(ctx, holder):
            continue
        if not task.paths or len(evidence.landed(ctx, task.paths)) != len(task.paths):
            continue
        closed += len(plan.settle_delivered(ctx, task.owner, task.paths, "the trunk"))
    return f"{closed} in-flight card(s) closed over work already on the trunk" if closed else ""


_REPAIRS = {
    "0001-task-paths": (1, _backfill_task_paths),
    "0002-quarantine-unreadable": (1, _quarantine_unreadable_state),
    "0003-absorb-scratch-todos": (1, _absorb_scratch_todos),
    "0004-lift-the-tool-call-ceiling": (1, _lift_the_tool_call_ceiling),
    "0005-trees-into-the-no-prompt-zone": (2, _move_trees_into_the_no_prompt_zone),
    "0006-drop-the-witness-timeout": (1, _drop_the_witness_timeout),
    "0007-forget-a-switch-taken-as-a-task": (1, _forget_a_statement_that_was_only_a_switch),
    "0008-collapse-the-decision-inbox": (1, _collapse_the_decision_inbox),
    "0009-drop-defects-that-are-not-ours": (1, _drop_defects_from_things_that_are_not_gates),
    "0010-shrink-unverified-reasons": (1, _shrink_unverified_reasons),
    "0011-close-shipped-cards": (1, _close_cards_whose_work_shipped),
}


# Checkbox items, in every shape markdown writes them: `-`, `*`, `+`, or `1.` before the
# box. Filename patterns were the first version of this and they missed an entire real
# setup — `docs/TODO.md`, `docs/pre-release-todo.md`, `.claude/commands/todo.md` — because
# nobody agreed to the naming convention the plugin was quietly expecting. What a
# registry looks like INSIDE is not a convention; it is markdown.
_OPEN_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[ \]\s+(?P<text>\S.*?)\s*$", re.M)
_DONE_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]\s", re.M)

# A template's checkboxes are a FORM, ticked in the pull request body on GitHub and never
# in the file. So it can never leave the list: `.github/pull_request_template.md` sat at
# "3 open item(s)" permanently and surfaced on every run, with no migration able to change
# the count. Reported as issue #63.
#
# Unlike the two conventions this feature invented and had to retract, these paths are
# GitHub's own and documented — every location it will read a template from, in the
# spellings it accepts. Not a guess about how somebody might name a file.
_TEMPLATE = re.compile(
    r"(?:^|/)(?:\.github/)?(?:"
    r"pull_request_template\.md|issue_template\.md|"
    r"PULL_REQUEST_TEMPLATE(?:/.+\.md|\.md)|ISSUE_TEMPLATE(?:/.+\.md|\.md)"
    r")$",
    re.I,
)

IGNORED = "adoption-ignored.json"

# Below this a document is prose that happens to contain a checkbox, not a registry.
MIN_ITEMS = 2


def open_items(text: str) -> list[str]:
    """The unfinished items a document is tracking."""
    return [m.group("text")[:plan_title_limit()] for m in _OPEN_ITEM.finditer(text)]


def _key(relative: str) -> str:
    """One spelling of a path, so `--ignore ./docs/x.md` and `--check docs/x.md` agree."""
    return PurePosixPath(relative.strip()).as_posix()


def _ignored(ctx: GitContext) -> dict:
    """Every document declared curated, from every checkout of this clone.

    Tier A lives inside the working tree, so this decision was per-worktree in a product
    whose premise is three to eight of them at once: `--ignore` in one tree, and every
    sibling went on counting the same document as untracked work forever (#98). It is the
    same fact and the same fix as `plan.load_all` — "this registry is ours" is true of the
    repository, so any checkout carrying the decision carries it for all of them.
    """
    merged: dict = {}
    for _path, record in _ignore_files(ctx):
        merged.update({_key(k): v for k, v in record.items() if isinstance(k, str)})
    return merged


def _ignore_files(ctx: GitContext) -> list[tuple[Path, dict]]:
    """Every checkout's ignore record, in the order a merge would apply them."""
    from .plan import sibling_worktrees

    out: list[tuple[Path, dict]] = []
    for root in sibling_worktrees(ctx) or [ctx.worktree_root]:
        path = root / store.TIER_A_DIRNAME / IGNORED
        record = store.read_json(path, default={})
        if isinstance(record, dict) and record:
            out.append((path, record))
    return out


def ignored_by(ctx: GitContext, relative: str) -> Path | None:
    """The file holding this decision, which is not always one this checkout has.

    "Delete that entry" is not an instruction a founder standing in a worktree can follow
    when the entry is in a sibling's copy and their own tree has no such file at all.
    """
    wanted = _key(relative)
    found = None
    for path, record in _ignore_files(ctx):
        if any(_key(k) == wanted for k in record if isinstance(k, str)):
            found = path
    return found


def is_ignored(ctx: GitContext, relative: str) -> bool:
    """Has the founder already said this document is theirs to keep?

    Read by every surface that would otherwise report it, because a decision one command
    honours and another contradicts is worse than no decision: `--ignore` said it would
    not be raised again and `--check` raised it in the next breath, with a non-zero exit
    a script could act on (#98).
    """
    return _key(relative) in _ignored(ctx)


def ignore(ctx: GitContext, relative: str, why: str = "curated by hand") -> None:
    """Declare a document none of the plugin's business, permanently.

    Without this the board nags about the same file every session forever, and a warning
    nothing can clear is one the founder learns to scroll past — which costs the warnings
    that matter. Tier A, because "this registry is ours, leave it alone" is a fact about
    the repository and should travel with it rather than be re-decided per clone.
    """
    record = store.read_json(store.tier_a(ctx, IGNORED), default={})
    if not isinstance(record, dict):
        record = {}
    # This checkout's own file, not the merged view: writing the union back would copy a
    # sibling's decisions onto this branch and commit them as if they had been made here.
    record[_key(relative)] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "why": why
    }
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
        if any(part in _SKIP for part in path.relative_to(root).parts) or _key(relative) in skip:
            continue
        if _TEMPLATE.search(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if POINTER in text or len(open_items(text)) < MIN_ITEMS:
            continue
        found.append(path)
    return found


def _not_ours_to_judge(ctx: GitContext, relative: str) -> bool:
    """The ledger's own documents, a form rather than a backlog, and anything curated."""
    return (
        relative.startswith(store.TIER_A_DIRNAME)
        or bool(_TEMPLATE.search(relative))
        or any(part in _SKIP for part in Path(relative).parts)
        or is_ignored(ctx, relative)
    )


def second_ledger(ctx: GitContext, target: Path, text: str) -> str:
    """Is this write standing a task registry up beside the ledger? The refusal, or "".

    The registry check ran at SessionStart and nowhere else, so it could only ever report
    documents that already existed. A session that CREATED one mid-session was told
    nothing: the duplicate was written, wired into three entry points and committed across
    two commits before a merge conflict with another session's migration made it visible
    (#103). The founder had asked for a TODO system "while the plugin does not support it",
    and neither of them noticed that it does.

    Deliberately narrow, because a false refusal here costs the founder a document they
    meant to write:

    - only when the ledger already holds tasks — an empty ledger means this may be how
      this repository starts tracking work, and SessionStart already says so;
    - only when the file does not exist yet, so editing or MIGRATING a registry that is
      already there is never refused;
    - never the ledger's own files, whose task documents are full of checkboxes;
    - never a document already declared curated, which is the standing answer to this.
    """
    from . import plan

    if target.exists() or not any(plan.summary(ctx).values()):
        return ""
    try:
        relative = target.resolve().relative_to(ctx.worktree_root.resolve()).as_posix()
    except (OSError, ValueError):
        return ""
    if _not_ours_to_judge(ctx, relative):
        return ""
    items = open_items(text)
    if POINTER in text or len(items) < MIN_ITEMS:
        return ""

    counts = plan.summary(ctx)
    held = sum(counts.values())
    return (
        f"claude-bestpractice: {relative} is a second place to track work — it holds "
        f"{len(items)} open item(s), and the ledger already holds {held}.\n"
        "  Two registries in one repository is the state this plugin exists to prevent: "
        "the sessions that read one never see the other, and it surfaces as a merge "
        "conflict rather than as a question.\n"
        f"  Put them in the ledger:  claude-bp-plan add \"<title>\"  (or `park` when "
        "another session will pick it up).\n"
        f"  Or, if this document is yours to curate by hand and the ledger is not the "
        f"place for it:  claude-bp-plan adopt --ignore {relative}"
    )


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


# What this can and cannot see, stated once so every surface can say the same thing.
#
# Twice now a shape was invented and quietly expected of the founder: first the filename
# `TODO-<name>.md`, then a checkbox list. Both were wrong in the same way — a convention
# nobody agreed to, presented as detection. A registry keyed by id and status is exactly
# what this feature is for, and it matches neither.
#
# So detection is best-effort and SAYS SO. The failure that matters is not missing a
# document; it is announcing that nothing was missed.
RECOGNISED = "checkbox lists (`- [ ] …`)"

# Said wherever a list of findings is shown, and NOT only where the list is empty — which
# is where v1.3.1 said it, and is the path that matters least. A repository with no
# checkbox document at all is one where nobody is mid-task; the mixed repository is where
# the message is believed, because a one-item list reads as a result rather than as an
# absence. That reading is what produced the field report this feature had to correct.
INCOMPLETE = (
    f"Only {RECOGNISED} are recognised; a registry in any other form is invisible here — "
    "point at one with `claude-bp-plan adopt --brief <file>`."
)


def unenumerable(text: str) -> bool:
    """True when a document tracks something this cannot count."""
    return not open_items(text) and not _DONE_ITEM.search(text)


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
    text = path.read_text(encoding="utf-8", errors="replace")
    if unenumerable(text):
        # Handing over a document with an empty item list and the words "for each item"
        # is instructions for nothing. Say what is actually true instead.
        return "\n".join([
            f"{relative} is not in a shape this can enumerate — it recognises only "
            f"{RECOGNISED}, and this is not one.",
            f"{migrated} task(s) in the ledger name it as their source.",
            "",
            "Read it yourself. For anything it tracks that is not already in the ledger:",
            "",
            f'  claude-bp-plan park "<title>" --paths <files> --note "<what is known, what',
            f'    was ruled out, where it stands>" --source {relative}',
            "",
            "`--check` can tell you how many tasks came from here, but NOT how many are",
            "left, because it cannot read this format. Do not take a count from it.",
            "",
            f"If it is curated and should stay the source of truth: "
            f"claude-bp-plan adopt --ignore {relative}",
        ])
    items = open_items(text)
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
    biggest = ""
    most = 0
    for path in registries(ctx):
        total, migrated = coverage(ctx, path)
        if total > migrated:
            left += total - migrated
            documents += 1
            if total - migrated > most:
                most = total - migrated
                biggest = path.relative_to(ctx.worktree_root).as_posix()
    if left:
        parts.append(f"{left} open item(s) in {documents} checkbox document(s)")

    if not parts:
        return ""
    # Name the next action, the way the worktree refusal names the destination instead of
    # describing the genre of thing to do (#27, restated as #65). `adopt` on its own is a
    # count repeated every session with nothing that starts anything, and a count nobody
    # can act on becomes a count nobody reads.
    #
    # Both exits are named, deliberately. A signal with only one exit is one a repository
    # that legitimately curates its documents can never discharge, and that is how a
    # channel gets tuned out — the same failure #63 was about, from the other direction.
    call = f"`claude-bp-plan adopt --brief {biggest}`" if biggest else "`claude-bp-plan adopt`"
    # "checkbox document(s)" is doing the work a whole sentence would otherwise do. This
    # line is injected into every session, so the honest scope has to be carried by the
    # words already there rather than by an extra one.
    return (
        " and ".join(parts) + " tracked outside the work ledger — "
        f"{call} to migrate, `adopt --ignore <paths>` if a document is curated and stays "
        "put; `adopt` alone lists them, including what it cannot see"
    )
