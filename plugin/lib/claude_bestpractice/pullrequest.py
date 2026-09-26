"""A pull request is an obligation, not a notification.

The failure this closes was described from a real repository: the session and the founder
agree on a change, the session opens a pull request, and then it stops — waiting for an
approval nobody asked it to wait for. The PR sits there. The session ends. Nothing in the
repository remembers it, so the next session does not pick it up either, and the work is
finished in every sense except the one that matters.

So opening a pull request records an obligation, and the obligation is discharged in
exactly one of two ways:

* **Merged.** The default. A session that opened a PR whose branch passes the final check
  merges it itself. There is no approval step, because there is no reviewer — that is the
  operating mode this whole plugin is built for.
* **Handed to the founder, with the blockers named.** If the final check finds something,
  the merge is REFUSED and the founder is told what and why.

The second half of that is the part with teeth, and it is deliberately not "fix it and
merge". A model asked to get a branch green at merge time will get it green, and the way
it does that is the founder's decision, not the model's: deleting an assertion, widening a
tolerance, or reverting the change that surfaced the problem all satisfy the letter. So
the gate refuses the merge and stops there. Whether to fix, and how, goes back to the
human who has to live with the answer.

Every check here is local and free — git state, the evidence ledger, the review findings
already on the board. Nothing on the tool-call path calls the network: that runs inside a
PreToolUse hook, and a gate that costs a round trip on every tool call is a gate that gets
switched off. The one question put to GitHub is the Stop gate's, once per branch, before it
says a pull request is missing (`missing`).

A draft is paused work. It is on the board, and nothing here asks for it, pushes it toward
a merge, or lets a `+merge` said about other work reach it until it is marked ready.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from . import config, store
from .gitctx import GitContext

PR_FILE = "pull-requests.jsonl"

OPEN = "open"
MERGED = "merged"
CLOSED = "closed"

# An obligation older than this is not an obligation, it is archaeology — the pull request
# was almost certainly merged or closed on the website and nothing here was told. Bounded
# on the same grounds as open items are: a warning nothing can clear teaches the founder
# to ignore the surface it appears on.
MAX_AGE_SECONDS = 30 * 24 * 3600

# Tool names, whatever server they arrive from. Claude Code exposes the same GitHub tools
# under differently-cased prefixes in one session, so the server segment is not matched.
_OPENS_TOOL = re.compile(r"(?:^|__)create_pull_request$")
_MERGES_TOOL = re.compile(r"(?:^|__)merge_pull_request$")

# The CLI spellings. `gh pr create` and `gh pr merge` reach exactly the same API, and a
# gate that watches only the structured tool is one an agent walks past on its first
# `Bash` call — which is how the credential check was walked past before it read heredocs.
# Kept as the fallback for a line the tokeniser cannot read. On a parseable line the
# decision is made on the PROGRAM being run, because these patterns matched text: `echo`
# of the invocation, `grep` for it in documentation, and a script carrying it as a JSON
# payload were all refused as merges — so the tool for investigating this gate was blocked
# by this gate (#76). Reading is not doing and quoting is not doing.
_OPENS_SHELL = re.compile(r"\bgh\s+pr\s+create\b")
_MERGES_SHELL = re.compile(r"\bgh\s+pr\s+merge\b(?:\s+(?P<number>\d+))?")

# Closing without merging, the discharge nothing recorded: `CLOSED` was defined and never
# written, so a pull request closed and its branch deleted stayed OPEN on every board for the
# thirty days `outstanding` keeps a record, as "ready to merge". The structured tool has no
# close of its own; it is `update_pull_request` with `state: closed`.
_CLOSES_TOOL = re.compile(r"(?:^|__)update_pull_request$")
_CLOSES_SHELL = re.compile(r"\bgh\s+pr\s+close\b(?:\s+(?P<number>\d+))?")

# Marked ready for review, or turned back into a draft with `--undo`. The structured tool
# does both through the same `update_pull_request`, with `draft: false` or `draft: true`.
_READIES_SHELL = re.compile(r"\bgh\s+pr\s+ready\b(?:\s+(?P<number>\d+))?")

# The flags of `gh pr close` and `gh pr ready` that take a value, so the value is not read
# as the pull request they name.
_FLAGS_WITH_VALUES = ("-c", "--comment", "-R", "--repo")

# The short flags of `gh pr create` that take no value, and so can share one dash with `-d`.
_CREATE_SWITCHES = frozenset("defw")


def _gh_subcommand(command: str, verb: str, pattern: "re.Pattern[str]"):
    """The argv of `gh pr <verb>` in this line, or None.

    Returns the regex match instead when the line cannot be tokenised — an unparseable
    line must not be a line that walks past the gate, so the old behaviour is what a
    failure falls back to.
    """
    from . import shellcmd

    parsed = shellcmd.commands(command)
    if not parsed:
        return pattern.search(command)
    found = shellcmd.runs(command, "gh", "pr", verb)
    return found[0] if found else None


def _records(ctx: GitContext) -> dict[str, dict[str, Any]]:
    """The latest record per branch. Append-only, so a later row supersedes."""
    latest: dict[str, dict[str, Any]] = {}
    for row in store.read_jsonl(store.tier_b(ctx, PR_FILE)):
        if isinstance(row, dict) and row.get("branch"):
            latest[str(row["branch"])] = row
    return latest


def _write(ctx: GitContext, record: dict[str, Any]) -> None:
    store.append_jsonl(store.tier_b(ctx, PR_FILE), record)


def opened(ctx: GitContext, branch: str, base: str, session_id: str,
           number: int = 0, url: str = "", draft: bool = False) -> None:
    """Record that this branch now has a pull request waiting on it.

    Recorded when the call is ALLOWED to proceed rather than after it returns, because a
    PreToolUse hook never sees the result. An obligation for a call that then failed is
    the cost of that, and it is a bounded one: the Stop gate hands it to the founder once
    and never blocks on it again.

    The number is this call's or none. A settled record's number belongs to the pull
    request that was merged or closed, and a branch that carries on gets a NEW one: carried
    over, it put "#41" on the board for #42, and a merge of #42 was judged against a number
    that no longer named anything open.

    `draft` is whether it was opened as one, which is what keeps paused work out of every
    demand until `redraft` hears it was marked ready (#239). And the board stops saying
    this branch has no pull request.
    """
    existing = _records(ctx).get(branch, {})
    if existing.get("state") == OPEN:
        return
    _write(ctx, {
        "branch": branch,
        "base": base,
        "number": number,
        "url": url,
        "session_id": session_id,
        "opened_at": time.time(),
        "state": OPEN,
        "draft": draft,
        "handed_off_at": 0.0,
    })
    close_answered(ctx, branch)


def drafted(tool_name: str, command: str, tool_input: dict[str, Any]) -> bool:
    """Does this call open its pull request as a draft?

    `draft: true` on the structured tool; `-d` or `--draft` on `gh pr create`, including a
    `-d` sharing its dash with the other switches, which is how `gh` reads `-fd`.
    """
    if _OPENS_TOOL.search(tool_name):
        return tool_input.get("draft") is True
    from . import shellcmd

    return any(
        _is_draft_flag(token)
        for argv in shellcmd.runs(command, "gh", "pr", "create") for token in argv[3:]
    )


def _is_draft_flag(token: str) -> bool:
    if token in ("--draft", "--draft=true"):
        return True
    if not token.startswith("-") or token.startswith("--"):
        return False
    for letter in token[1:]:
        if letter == "d":
            return True
        if letter not in _CREATE_SWITCHES:
            return False
    return False


def number_in(payload: Any) -> int:
    """The pull request number in whatever a create call answered with, or 0.

    Two shapes reach this from one event: a structured tool returns an object, and
    `gh pr create` prints the URL on stdout. Both end in `/pull/<n>`, so the URL is the
    reliable half and a `number` field is taken when it is there.
    """
    if isinstance(payload, dict):
        found = payload.get("number")
        if isinstance(found, int) and found > 0:
            return found
    text = payload if isinstance(payload, str) else store.dumps(payload)
    match = re.search(r"/pull/(\d+)", str(text))
    return int(match.group(1)) if match else 0


def learn_number(ctx: GitContext, branch: str, number: int) -> bool:
    """Fill in the number of an obligation already on record. True when it landed.

    The number is the only thing that makes "is this merge about this branch?" decidable
    without a network call, and the request that opens a pull request does not carry it.

    The latest answer wins. A write-once guard was tried and taken back out: `opened`
    already refuses to replace an OPEN record, so a second pull request on the same branch
    keeps the first record — and pinning the first NUMBER onto it would leave the gate
    judging merges against a pull request that no longer exists.
    """
    record = _records(ctx).get(branch)
    if not record or record.get("state") != OPEN:
        return False
    if number <= 0:
        return False
    _write(ctx, {**record, "number": number})
    return True


def settle(ctx: GitContext, branch: str, state: str) -> None:
    """Discharge the obligation — merged, or closed without merging."""
    record = _records(ctx).get(branch)
    if record and record.get("state") == OPEN:
        _write(ctx, {**record, "state": state, "settled_at": time.time()})


def landed(ctx: GitContext, record: dict[str, Any]) -> bool:
    """Has this record's work already reached the trunk, whatever happened to the record?

    Nothing here watches GitHub. The obligation is discharged by `settle`, which is
    reached from the one merge this session performs — so a pull request merged from the
    website, from another clone, or by `gh pr merge` in a shell this gate could not parse
    leaves a record saying OPEN forever. The founder then gets the Stop demand for a pull
    request that was merged and whose branch was deleted, told to report a merge that had
    already happened (#205).

    Two answers, and the second is the one that matters. An ANCESTOR test settles an
    ordinary merge exactly. A SQUASH rewrites the commits, so the branch tip is an
    ancestor of nothing — and squash is what `gh pr merge --squash --delete-branch` does,
    which is the line in the report. For that, the question is asked of the CONTENT: every
    file this branch delivers is byte-identical to what the trunk holds.

    Deliberately conservative in three ways, because settling an obligation that is really
    open costs the founder the one reminder they get. The content test is only asked of
    the tree standing on that branch, since `evidence.landed` reads THIS checkout's HEAD;
    a branch that delivers no file is never settled by it; and a partial match settles
    nothing. What it cannot distinguish — somebody else landing this exact content — is
    the work reaching the trunk either way, which is what the obligation was about.
    """
    from . import evidence
    from .gitctx import is_ancestor

    branch = str(record.get("branch") or "")
    if not branch:
        return False
    base = str(record.get("base") or "") or "main"
    for trunk in (f"origin/{base}", "origin/HEAD"):
        if is_ancestor(ctx, branch, trunk):
            return True
    if branch != ctx.branch:
        return False
    delivered = delivered_paths(ctx, base, branch)
    return bool(delivered) and len(evidence.landed(ctx, delivered)) == len(delivered)


def reconcile(ctx: GitContext, branch: str = "") -> list[str]:
    """Settle every open record whose work is already on the trunk, or whose branch is gone.
    The branches settled.

    Called by the surfaces that ACT on an open pull request — the Stop gate and the status
    line — rather than from `outstanding`, so reading the board never writes to it. The
    trunk test runs for one branch when named, because the Stop gate only ever asks about
    the one it is standing on, and `landed`'s content test can only answer for that tree.

    A branch this clone no longer has anywhere — not as a branch, not as a remote-tracking
    ref — is asked of every record, because that answer is the same from any tree. It is
    what a pull request closed on the website, or merged there with its branch deleted,
    leaves behind: a nine-day-old record for a deleted branch was on every session start as
    "no movement" and on the board as "ready to merge", and nothing could clear it. What it
    cannot tell apart is a branch that only ever existed on GitHub and was never fetched.
    """
    settled: list[str] = []
    live = outstanding(ctx)
    present = _branches_here(ctx) if live else None
    for record in live:
        name = str(record.get("branch") or "")
        if present is not None and name not in present:
            settle(ctx, name, CLOSED)
        elif (not branch or name == branch) and landed(ctx, record):
            settle(ctx, name, MERGED)
        else:
            continue
        settled.append(name)
    return settled


def _branches_here(ctx: GitContext) -> set[str] | None:
    """Every branch name this clone knows, local or remote-tracking. None when git cannot say.

    One call for every record, and None rather than an empty set on failure: an unreadable
    ref store must not read as every branch deleted.
    """
    from .gitctx import _status

    code, listed = _status(
        ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/"], ctx.worktree_root
    )
    if code != 0:
        return None
    names: set[str] = set()
    for ref in listed.splitlines():
        if ref.startswith("refs/heads/"):
            names.add(ref[len("refs/heads/"):])
        elif ref.startswith("refs/remotes/"):
            # refs/remotes/<remote>/<branch>, and the branch keeps any slashes of its own.
            names.add(ref.split("/", 3)[-1])
    return names


def closes(ctx: GitContext, tool_name: str, command: str, tool_input: dict[str, Any],
           cwd: str = "") -> str:
    """The branch whose open pull request this call closes without merging, or "".

    `gh pr close` names what it closes by number, by URL, by branch, or not at all — which
    is the current branch, the same way `gh pr merge` reads it. A number only resolves
    through a record that learned it; one that did not is left to `reconcile`, which finds it
    once the branch is deleted. A call naming another repository closes nothing here: its
    numbers and branch names are that repository's.
    """
    branch = _closed_branch(ctx, tool_name, command, tool_input, cwd)
    if branch and about_this_repository(ctx, tool_name, tool_input, command):
        return branch
    return ""


def _closed_branch(ctx: GitContext, tool_name: str, command: str, tool_input: dict[str, Any],
                   cwd: str) -> str:
    """What `closes` reads out of the call, before asking whose repository it is about."""
    if _CLOSES_TOOL.search(tool_name):
        if str(tool_input.get("state") or "").lower() != "closed":
            return ""
        return _numbered(ctx, _as_number(tool_input.get("pullNumber")))
    found = _gh_subcommand(command, "close", _CLOSES_SHELL)
    if found is None:
        return ""
    return _branch_named(ctx, found, command, cwd)


def readiness(ctx: GitContext, tool_name: str, command: str, tool_input: dict[str, Any],
              cwd: str = "") -> tuple[str, bool] | None:
    """The branch whose open pull request this call marks ready or turns back into a draft,
    and whether it is a draft after it. None when the call does neither.

    `gh pr ready` names its pull request the way `gh pr close` does, and `--undo` is the
    way back. The structured tool says it with `draft` on `update_pull_request`. Read so a
    draft that is marked ready becomes an ordinary obligation again, with the one demand it
    is owed, instead of staying paused on the board after the work went on (#239).
    """
    if _CLOSES_TOOL.search(tool_name):
        draft = tool_input.get("draft")
        if not isinstance(draft, bool):
            return None
        branch = _numbered(ctx, _as_number(tool_input.get("pullNumber")))
    else:
        found = _gh_subcommand(command, "ready", _READIES_SHELL)
        if found is None:
            return None
        draft = "--undo" in (found[3:] if isinstance(found, list) else command.split())
        branch = _branch_named(ctx, found, command, cwd)
    if not branch or not about_this_repository(ctx, tool_name, tool_input, command):
        return None
    return branch, draft


def redraft(ctx: GitContext, branch: str, draft: bool) -> None:
    """Record that this branch's open pull request is now a draft, or now ready for review."""
    record = _records(ctx).get(branch)
    if record and record.get("state") == OPEN and bool(record.get("draft")) != draft:
        _write(ctx, {**record, "draft": draft})


def _branch_named(ctx: GitContext, found, command: str, cwd: str) -> str:
    """The branch of the pull request a `gh pr close` or `gh pr ready` names.

    By number or URL through the record that learned it, by branch as written, or by
    naming none, which is the branch checked out where the command runs.
    """
    selector = _selector(found)
    if not selector:
        return _branch_of(_directory_of(command) or cwd) or ctx.branch
    number = int(selector) if selector.isdigit() else number_in(selector)
    return _numbered(ctx, number) if number else selector


def _selector(found) -> str:
    """What `gh pr close` or `gh pr ready` was pointed at: a number, a URL, a branch, or ""."""
    if not isinstance(found, list):
        return found.group("number") or ""
    tokens = iter(found[3:])
    for token in tokens:
        if token in _FLAGS_WITH_VALUES:
            next(tokens, None)
        elif not token.startswith("-"):
            return token
    return ""


def _as_number(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _numbered(ctx: GitContext, number: int) -> str:
    """The branch of the open obligation carrying this pull request number, or ""."""
    if number <= 0:
        return ""
    for name, record in _records(ctx).items():
        if record.get("state") == OPEN and _as_number(record.get("number")) == number:
            return name
    return ""


def outstanding(ctx: GitContext) -> list[dict[str, Any]]:
    """Every pull request this clone knows about and has not seen the end of."""
    now = time.time()
    live = [
        row for row in _records(ctx).values()
        if row.get("state") == OPEN and now - float(row.get("opened_at") or 0) < MAX_AGE_SECONDS
    ]
    live.sort(key=lambda r: float(r.get("opened_at") or 0), reverse=True)
    return live


def branches_without_one(ctx: GitContext, hours: float) -> list[tuple[str, float]]:
    """Branches carrying unmerged commits that no pull request here has ever been about.

    (branch, age in hours of its last commit), oldest first. The live example from the
    reporting repository: `chore/untrack-ledger-plan-files`, one commit, no pull request
    ever opened, a hundred and twenty-six commits behind the trunk by the time anybody
    noticed — and it was fixing the very problem that three later issues were about.
    Nothing showed it: not the board, not `gh pr list`, only `git branch` (#220).

    ONE git call, whatever the branch count. `--no-merged` is what makes that possible, and
    at a hundred and thirty-five local branches the alternative — asking git about each one
    — is a hundred and thirty-five processes on every session start.

    What it cannot know is a pull request opened on the website: nothing here calls the
    network. So the line that reports this says "no pull request this clone has seen" and
    names the command that asks GitHub, rather than asserting there is none.

    A branch a live session is standing on is never named, whatever its age. This line is
    about work nobody is coming back to, and somebody is on that one right now.
    """
    if hours <= 0:
        return []
    from . import sessions
    from .gitctx import trunk_ref
    from .gitpolicy import TRUNK_NAMES

    trunk = trunk_ref(ctx)
    if not trunk:
        return []
    known = set(_records(ctx))
    try:
        known.update(record.branch for record in sessions.live_sessions(ctx) if record.branch)
    except Exception:  # noqa: BLE001 - a naming line must never fail a session start
        return []
    now = time.time()
    out = [
        (branch, (now - committed) / 3600.0)
        for branch, committed in _unmerged_branches(ctx, trunk)
        if branch not in known and branch not in TRUNK_NAMES
        and (now - committed) / 3600.0 >= hours
    ]
    return sorted(out, key=lambda row: row[1], reverse=True)


def _unmerged_branches(ctx: GitContext, trunk: str) -> list[tuple[str, float]]:
    """Local branches not merged into `trunk`, with the unix time of their last commit."""
    from .gitctx import _run

    try:
        raw = _run(
            ["for-each-ref", f"--no-merged={trunk}",
             "--format=%(refname:short)%09%(committerdate:unix)", "refs/heads/"],
            ctx.worktree_root, check=False,
        )
    except Exception:  # noqa: BLE001 - a naming line must never fail a session start
        return []
    rows: list[tuple[str, float]] = []
    for line in raw.splitlines():
        name, _, when = line.partition("\t")
        if name.strip() and when.strip().isdigit():
            rows.append((name.strip(), float(when.strip())))
    return rows


def idle(ctx: GitContext, days: float) -> list[tuple[int, str, float]]:
    """Open pull requests nothing has moved: (number, branch, age in days), oldest first.

    Three of them sat for a month on the reporting repository — 21.08, 29.08, 31.08 — and
    the only surface that knew was GitHub's own list, which nobody was reading. Read from
    the obligations this clone already records, so it costs no network call and no process.
    """
    if days <= 0:
        return []
    now = time.time()
    out = [
        (int(row.get("number") or 0), str(row.get("branch") or ""),
         (now - float(row.get("opened_at") or now)) / 86400.0)
        for row in outstanding(ctx)
    ]
    return sorted([row for row in out if row[2] >= days], key=lambda row: row[2], reverse=True)


def unhanded(ctx: GitContext, branch: str) -> dict[str, Any] | None:
    """This branch's open pull request, if it is ready and the founder has not been told.

    Not a draft. A draft is how work is paused, and the demand this feeds says "merge it" or
    "show it to the founder for their `+merge`" — both of them a push toward a merge the
    founder asked to hold (#239). Left unhanded rather than handed off, so the demand is
    still there, once, when the draft is marked ready.
    """
    record = _records(ctx).get(branch)
    if not record or record.get("state") != OPEN or record.get("draft"):
        return None
    return None if record.get("handed_off_at") else record


def handed_off(ctx: GitContext, branch: str) -> bool:
    """Has this branch's pull request already been put to the founder?

    `stop_demand` tells the session to report exactly what is in the way and stop, and not
    to push changes to make the check pass. Once that has been said, a session standing
    still on an unchanged tree is a session doing as it was told — which is why the evidence
    gate reads this before refusing the same thing a third and fourth time (#206).
    """
    record = _records(ctx).get(branch) or {}
    return bool(record.get("handed_off_at"))


def hand_off(ctx: GitContext, record: dict[str, Any], blockers: list[str]) -> None:
    """Mark the obligation as surfaced, so it is raised once and then carried.

    Called BEFORE the block that raises it, not after. A session that ignores the block,
    crashes, or hits the escalation ceiling must not meet the same block on its next Stop
    — one unignorable interruption per pull request is the whole budget, and past that the
    board is what keeps it from being forgotten.
    """
    _write(ctx, {**record, "handed_off_at": time.time(), "blockers": blockers[:8]})


def blockers(ctx: GitContext, base: str, head: str = "") -> list[str]:
    """Everything standing between this branch and a merge, in plain language.

    `delivery.ready` is the same check `claude-bp-ship --pr` runs before opening one, so a
    branch cannot pass at open time and silently fail at merge time for a different reason.
    On top of it: the review findings already on the board, which are the ones a human
    reviewer would have raised if this repository had a human reviewer.

    Only review findings — not every open item. This module writes an open item of its own
    when it hands a pull request to the founder, and counting that would make an open pull
    request its own reason for not being merged: refused forever, by itself, for existing.
    Unverified finishes are left to `delivery.ready`, which already reads their ledger.
    """
    from . import delivery

    branch = head or ctx.branch
    # A record whose head IS its base is misfiled, not strict — the branch can never gain
    # commits over itself, so the gate would refuse forever (#79). Judging it on the
    # session's tree is the older, wronger answer; saying nothing is the honest one.
    if branch == base:
        return []
    problems = (
        list(delivery.ready(ctx, base)) if branch == ctx.branch
        else _about_the_pull_request(ctx, base, branch)
    )
    # Nothing removes an unverified finish, so the list the merge refusal promised to empty
    # could not empty, and the founder's `+merge` after it was put to them changed nothing
    # (#243). Whether to merge work that finished unproven is theirs, and that word is it.
    unverified = [problem for problem in problems if problem.endswith(delivery.UNVERIFIED)]
    if unverified and _finish_accepted(ctx, branch):
        problems = [problem for problem in problems if problem not in unverified]
    problems.extend(_findings(ctx, base, branch))
    return problems


# When the founder's `+merge` named a pull request whose unverified finish had been put to
# them: the Stop gate's hand-off listed it, or a refused merge did.
FINISH_ACCEPTED = "unverified_accepted_at"

# When a merge of this pull request was last refused over its blockers, which that refusal
# told the session to take to the founder.
REFUSED = "refused_at"


def _finish_accepted(ctx: GitContext, branch: str) -> bool:
    """Has the founder accepted this branch's latest unverified finish, having been shown it?"""
    from . import delivery

    latest = delivery.last_unverified(ctx, branch)
    record = _records(ctx).get(branch) or {}
    accepted = float(record.get(FINISH_ACCEPTED) or 0)
    return (latest is not None and record.get("state") == OPEN
            and accepted > 0 and accepted >= latest)


def _accept_its_finish(ctx: GitContext, record: dict[str, Any]) -> None:
    """Record the founder's word as their decision on the finish they were shown, if any.

    Shown, not merely filed: a `+merge` said about the pool before anybody told them one of
    its pull requests finished unproven is not a decision about that. The refusal that lists
    it is what shows it to them, and their next word is.
    """
    from . import delivery

    latest = delivery.last_unverified(ctx, str(record.get("branch") or ""))
    shown = max(float(record.get("handed_off_at") or 0), float(record.get(REFUSED) or 0))
    if latest is not None and shown > 0 and shown >= latest:
        _write(ctx, {**record, FINISH_ACCEPTED: time.time()})


def note_refusal(ctx: GitContext, branch: str) -> None:
    """Record that this branch's blockers were just put to the founder by a refused merge."""
    record = _records(ctx).get(branch)
    if record and record.get("state") == OPEN:
        _write(ctx, {**record, REFUSED: time.time()})


def _findings(ctx: GitContext, base: str, branch: str) -> list[str]:
    """Review findings that are still this pull request's problem.

    Three ways one stops being that, each earned: it is about a file the pull request does
    not touch (#69), the founder has ruled it false (#75), or the rule that raised it no
    longer fires (#80).
    """
    from . import board, drafts, provenance

    out: list[str] = []
    in_diff = _files_against(ctx, base, branch)
    for item in board.open_items(ctx, branch=branch):
        if item.get("provenance") != provenance.FRESH or not str(item.get("id", "")).startswith("review-"):
            continue
        # Only findings in files this pull request actually changes. The workflow REQUIRES
        # `git merge origin/main` before merging, and that import brought every open
        # finding in main onto the branch — a pull request of eight markdown files was
        # refused over SQL interpolation in a Python module it never touched, and the
        # longer main got the more it inherited, so syncing with main could never go green
        # (#69). Subjects are compared against the diff from the merge base, which is the
        # pull request's own diff and not "every file the branch's commits touched".
        # `drafts.subject_paths`, not a plain read: `provenance.stamp` stores these as
        # dicts carrying a blob hash, and reading them as strings gives an empty list for
        # every real item. That is the defect that helper was written for, and doing it by
        # hand here would have silently dropped every finding instead of the stale ones.
        subjects = drafts.subject_paths(item)
        if subjects and in_diff is not None and not (set(subjects) & in_diff):
            continue
        # A finding the founder has ruled out is not a blocker. Without this the only ways
        # to clear a false positive were to rewrite correct code or to stop using the gate,
        # and a permanent block over code that is right is how a gate gets switched off.
        text = str(item.get("text", ""))
        if _all_dismissed(ctx, text, subjects):
            continue
        # And one whose RULE no longer fires is not a blocker either. A finding is a claim
        # about code as it stands, so fixing a detector has to clear what the broken
        # detector filed — the `sql-interpolation` corrected in #78 was still counted ten
        # sightings later, over code the current rule reads as clean (#80).
        if _stale(ctx, text, subjects):
            board.close_open_item(ctx, str(item.get("id", "")))
            continue
        out.append(text[:200])
    return out


def _all_dismissed(ctx: GitContext, text: str, subjects: list[str]) -> bool:
    """Has every detector named in this item been ruled out for every file it names?

    Conservative on both axes: an item naming a detector this cannot parse, or one path
    that is still live, stays a blocker. Silence is the wrong way to be wrong here.
    """
    from . import board

    ruled_out = board.dismissed(ctx)
    if not ruled_out or not subjects:
        return False
    detectors = {part.split(" in ")[0].strip() for part in text.split(":", 1)[-1].split(",")}
    detectors = {d for d in detectors if d}
    if not detectors:
        return False
    return all(f"{d}:{p}" in ruled_out for d in detectors for p in subjects)


def _stale(ctx: GitContext, text: str, subjects: list[str]) -> bool:
    """Has every rule this finding names stopped firing on every file it names?

    Conservative in the same direction as `_all_dismissed`: a detector this cannot parse,
    a file it cannot read, or one path where the rule still fires all keep the finding.
    Retiring on the strength of not knowing is how a real finding disappears.
    """
    from . import reviewrules

    detectors = _detectors_in(text)
    if not detectors or not subjects:
        return False
    return not any(
        reviewrules.still_fires(ctx.worktree_root, detector, path)
        for detector in detectors for path in subjects
    )


def _detectors_in(text: str) -> set[str]:
    """The rule names an item's summary lists, out of `<name> in <path>, <name> in <path>`."""
    named = {part.split(" in ")[0].strip() for part in text.split(":", 1)[-1].split(",")}
    return {name for name in named if name and " " not in name}


def _about_the_pull_request(ctx: GitContext, base: str, head: str) -> list[str]:
    """The blockers that are facts about `head`, when the session is standing elsewhere.

    A merge is not a write to a working tree, and a session in a main checkout is the
    normal case for anything that coordinates work — reading pull requests, merging,
    releasing. Judging the merge on the occupied tree refused every one of them, and each
    reason named the wrong subject (#74): "no commits on top of main" measured on a
    checkout that is not supposed to carry any, an UNVERIFIED finish belonging to a
    different session's task hours earlier, and findings in files the pull request never
    touches.

    Deliberately a SUBSET of `delivery.ready`. Two of its checks are about a working tree
    rather than a branch — uncommitted changes, and the red-suite record written per tree —
    and a tree the pull request has nothing to do with cannot speak for it. Everything that
    is genuinely about the branch is still asked, of the branch.
    """
    from . import delivery, evidence

    problems: list[str] = []
    if not delivery.commits_since(ctx, base, head):
        problems.append(f"no commits on {head} over {base}")
    said = evidence.red_problem(ctx, head)
    if said:
        problems.append(said)
    unproven = evidence.unproven(ctx, head)
    if unproven:
        problems.append(unproven)
    if delivery.unverified_on(ctx, head):
        problems.append(f"{head} {delivery.UNVERIFIED}")
    return problems


def _files_against(ctx: GitContext, base: str, head: str = "HEAD") -> set[str] | None:
    """Paths this branch changes relative to its merge base with `base`.

    None when git cannot answer — an unknown base, an unborn branch — and the caller then
    keeps every finding. Losing a real finding is worse than repeating a stale one, so the
    filter only ever narrows on an answer it actually got.

    `origin/<base>` first, and the local branch only where there is no remote one. A pull
    request is measured against the remote base, and a local trunk is wherever somebody last
    fast-forwarded it: in a fresh clone it sat 19 commits behind, so a 20-file branch was
    measured as 81. Wider was safe while every caller filtered review findings with this; it
    is not since `settle_delivered` closes cards with it, where a merge of one file closed a
    card over somebody else's already-merged release (card 0061). The first ref git can
    answer for is the answer, empty included — falling through on an empty diff is how the
    stale ref got asked.
    """
    from .gitctx import _status

    for ref in (f"origin/{base}", base):
        code, listed = _status(["diff", "--name-only", f"{ref}...{head or 'HEAD'}"], ctx.worktree_root)
        if code == 0:
            return {line.strip() for line in listed.splitlines() if line.strip()}
    return None


def delivered_paths(ctx: GitContext, base: str, head: str = "") -> list[str]:
    """The files a merge of `head` into `base` would carry, or [] when git cannot say.

    Public because the ledger asks it. A card names the files it is about, so the only
    mechanical answer to "did this merge finish that card" is whether the merge carried
    them — and empty is the right answer to fall back on, because it closes nothing.
    """
    found = _files_against(ctx, base, head or "HEAD")
    return sorted(found) if found else []


def merge_refusal(record: dict[str, Any], problems: list[str]) -> str:
    """Why this merge is refused, and what the model is to do instead of fixing it."""
    named = record.get("number") and f"#{record['number']}" or record.get("branch", "this branch")
    listed = "\n".join(f"  - {p}" for p in problems[:6])
    return (
        f"claude-bestpractice: refusing to merge {named} — the final check found "
        f"{len(problems)} thing(s) in the way:\n{listed}\n"
        "Tell the founder exactly this and stop. Do NOT merge, and do NOT push changes to "
        "make the check pass: at merge time there are several ways to go green — weaken an "
        "assertion, widen a tolerance, revert the change that surfaced the problem — and "
        "which one is acceptable is the founder's call, not yours.\n"
        "Once they have decided, this gate allows the merge as soon as the list above is "
        f"empty.{_how_a_finish_is_decided(problems)}"
    )


def _how_a_finish_is_decided(problems: list[str]) -> str:
    """The one item on a merge's list that no change empties, and the word that does."""
    from . import delivery

    if not any(problem.endswith(delivery.UNVERIFIED) for problem in problems):
        return ""
    return ("\nAn UNVERIFIED finish is theirs to accept as it stands: a `+merge` they send "
            "after seeing this is that decision, and takes it off the list.")


def stop_demand(record: dict[str, Any], problems: list[str], accepted: bool = True) -> str:
    """The one interruption a pull request gets: merge it, or say why it cannot be.

    `accepted` is whether a `+merge` on record covers this pull request, and without it
    this does not demand a merge at all. The demand used to read "there is no reviewer and no approval
    step in this repository", which was true about GitHub and false about the product: the
    reviewer is the founder and the review happens in the chat. So a session that had been
    told "не кати, буду смотреть" was instructed on every turn to merge anyway, and had to
    argue its way out in writing (#140).

    It is a record, NOT a person's state of mind, and the difference is the whole of #192.
    The flag is repository-wide and names no pull request, so this text says what is on
    record and lets the reader decide whether it was meant here — it does not report that
    the founder accepted anything.
    """
    named = f"#{record['number']}" if record.get("number") else f"on {record.get('branch', '')}"
    if not problems and not accepted:
        return (
            f"claude-bestpractice: pull request {named} is open, passes every check, and is "
            "waiting for the founder rather than for you.\n"
            "Show them what changed — the outcome, not the diff — and leave it open. When "
            "they are happy they say `+merge`, and you then merge it yourself without "
            "asking again.\n"
            "This will not be raised again."
        )
    if not problems:
        # WHAT IS ON RECORD, never what the founder feels. The line here read "the founder
        # has accepted it" and "Their word is already given" — a claim about a person,
        # made by a gate that cannot see one. It is not merely unverified, it is
        # unverifiable: the record is one repository-wide flag with no pull request
        # attached, so even a real `+merge` may have been given for other work in another
        # session of the same clone.
        #
        # It was believed over a check that contradicted it. The session read an empty
        # `reviewDecision`, was told by this sentence that the word was already given,
        # and merged two pull requests the founder had explicitly asked it to leave alone
        # (#192). A gate that asserts a fact it cannot hold outranks the model's own
        # evidence, which is the opposite of what this plugin is for.
        return (
            f"claude-bestpractice: pull request {named} is open, every check passes, and this "
            "turn was about to end without it being merged. A `+merge` that covers it is "
            "on record in this repository.\n"
            "That record cannot say what the founder had in mind, so it may have been "
            "given for other work: if it was meant for this one, merge it now — a pull "
            "request left open is work that is finished everywhere except where it counts.\n"
            "If it was not, or you believe this must not be merged yet, say so to the "
            "founder in one line and leave it open; this will not be raised again."
        )
    listed = "\n".join(f"  - {p}" for p in problems[:6])
    return (
        f"claude-bestpractice: pull request {named} is open and cannot be merged — the final "
        f"check found {len(problems)} thing(s):\n{listed}\n"
        "Report exactly this to the founder and stop. Do NOT push changes to make the check "
        "pass — how to resolve these is their decision, because the fixes that make a branch "
        "green at merge time are often ones nobody wanted.\n"
        "This will not be raised again; it is now on the board until the pull request is "
        f"merged or closed.{_how_a_finish_is_decided(problems)}"
    )


# Raised once per branch, and the marker is per clone rather than per session: a founder
# who says "not yet" and starts a new chat is not asking to be told again.
DEMANDS = "pull-request-demands.json"


def known(ctx: GitContext, branch: str) -> bool:
    """Has a pull request for this branch ever been recorded, in any state?

    Any state, deliberately. A merged record means the work reached the base branch, and
    a local checkout whose base is behind still counts commits on top of it — so asking
    "is one open" here would demand a second pull request for work that has already
    landed.
    """
    return branch in _records(ctx)


def demanded(ctx: GitContext, branch: str) -> bool:
    record = store.read_json(store.tier_b(ctx, DEMANDS), default={})
    return branch in record if isinstance(record, dict) else False


def note_demand(ctx: GitContext, branch: str) -> None:
    path = store.tier_b(ctx, DEMANDS)
    record = store.read_json(path, default={})
    if not isinstance(record, dict):
        record = {}
    record[branch] = int(time.time())
    store.write_json(path, record)


# The id every "NO PULL REQUEST" item on the board is filed under, by the demand above.
MISSING_ITEM = "pr-missing-"


def close_answered(ctx: GitContext, branch: str = "") -> int:
    """Close the board's "NO PULL REQUEST" items that a pull request on record answers.
    How many; `branch` limits it to that one.

    Filed with the demand to open one and never closed once it was: the board said "NO PULL
    REQUEST for finished work on X" beside "OPEN PULL REQUESTS: #N on X" for the fourteen
    days an item is kept (#239).
    """
    from . import board

    records = _records(ctx)
    closed = 0
    for item in board.open_items(ctx, branch=branch or None, with_provenance=False, limit=None):
        if str(item.get("id", "")).startswith(MISSING_ITEM) and item.get("branch") in records:
            board.close_open_item(ctx, str(item["id"]))
            closed += 1
    return closed


# How long the Stop gate waits for GitHub to say whether a branch already has a pull request.
_GH_LOOK_SECONDS = 10


def missing(ctx: GitContext) -> tuple[str, bool] | None:
    """What the checked-out branch's finished work lacks a pull request against: (base,
    whether GitHub was asked). None when nothing is missing.

    Nothing is when one is on record, when it was already demanded, when the branch is not
    ready to open one — or when GitHub has one open, which is then recorded here so the
    board carries it and the Stop gate treats it as any other. The record only knows the
    pull requests a hook in this clone saw opened, and one opened in a terminal, on the
    website or from another clone was answered with "no pull request against main" and an
    order to open a second one (#239). Asked once per branch, and only here, where the
    alternative is a demand.
    """
    branch = ctx.branch
    if known(ctx, branch) or demanded(ctx, branch):
        return None
    from . import delivery, gitpolicy

    base = gitpolicy.default_branch(ctx) or "main"
    if delivery.ready(ctx, base):
        return None
    listed = on_github(ctx, branch)
    if not listed:
        return base, listed is not None
    found = next((row for row in listed if row.get("baseRefName") == base), listed[0])
    # Opened by no session here, so no chat's `+merge` names it (decision 0023).
    opened(ctx, branch, str(found.get("baseRefName") or base), "",
           number=_as_number(found.get("number")), url=str(found.get("url") or ""),
           draft=found.get("isDraft") is True)
    return None


def on_github(ctx: GitContext, branch: str) -> list[dict[str, Any]] | None:
    """The open pull requests GitHub has from `branch`. None when it could not be asked.

    Through `gh`, where the founder's own credentials already are, with a deadline: a `gh`
    that hung once held `claude-bp status` for a minute. Unknown is an answer, and the
    caller says so rather than guessing either way.
    """
    import json
    import shutil
    import subprocess

    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [gh, "pr", "list", "--head", branch, "--state", "open",
             "--json", "number,url,isDraft,baseRefName"],
            cwd=str(ctx.worktree_root), stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=_GH_LOOK_SECONDS,
        )
        listed = json.loads(proc.stdout or "[]") if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(listed, list):
        return None
    return [row for row in listed if isinstance(row, dict)]


def open_demand(branch: str, base: str, asked: bool = True, accepted: bool = False) -> str:
    """The turn ended with finished work that never became a pull request.

    The measured shape is not an agent that forgets. It is an agent that asks — the idea,
    the checks and the commits all went past the founder in the chat, and then the last
    step arrived as a question they had already answered three times over. Opening one is
    this plugin's own convention, so asking about it is the plugin making the founder
    confirm its own rule, and the vouch in `pre-tool` means there is not even a permission
    prompt left to hide behind.

    It says what it knows and nothing past it. `asked` is whether GitHub answered, and the
    text claims no more than that. Opening is the session's, the merge is the founder's
    word (decision 0010), and `accepted` is whether a `+merge` is on record. It used to end
    with "merge it yourself once the checks pass", and it said so over a draft the founder
    had asked to pause (#239).
    """
    where = (" or on GitHub" if asked else
             f"; GitHub could not be asked, so if one was opened elsewhere, "
             f"`gh pr list --head {branch}` shows it and there is nothing to open")
    merge = (
        "A `+merge` is on record in this repository, and the record cannot say what the "
        "founder had in mind: if it was meant for this work, merge it once the checks pass; "
        "if not, show them what changed and leave it open."
        if accepted else
        "Then show the founder what changed and leave it open: it is merged on their "
        "`+merge`, not on green checks."
    )
    return (
        f"claude-bestpractice: {branch} carries committed work that passes every check, and no "
        f"pull request for it against {base} is on record here{where}.\n"
        "Open it now, and do not ask whether to: this repository's convention is "
        "commit -> pull request -> merge, this gate approves opening one without a prompt, "
        "and a question the founder has already answered by asking for the work is not a "
        "decision they own:\n"
        f"  gh pr create --base {base} --fill\n"
        "If the founder has paused this work, add `--draft`: a draft is left alone until it "
        "is marked ready. `claude-bp-ship --pr` opens one with a body written for them.\n"
        f"{merge}\n"
        "This will not be raised again for this branch."
    )


def line(ctx: GitContext) -> str:
    """The board's reminder that a pull request is still waiting. Empty when none is."""
    live = outstanding(ctx)
    if not live:
        return ""
    shown = []
    for row in live[:3]:
        named = f"#{row['number']}" if row.get("number") else str(row.get("branch", ""))
        state = "blocked" if row.get("blockers") else "ready to merge"
        if row.get("draft"):
            state = "draft"
        shown.append(f"{named} on {row.get('branch', '')} ({state})")
    more = f" (+{len(live) - 3} more)" if len(live) > 3 else ""
    return "OPEN PULL REQUESTS: " + "; ".join(shown) + more


def opens_a_pull_request(tool_name: str, command: str) -> bool:
    return bool(
        _OPENS_TOOL.search(tool_name)
        or _gh_subcommand(command, "create", _OPENS_SHELL) is not None
    )


def _number(found) -> int:
    """The pull request number out of either shape `_gh_subcommand` returns.

    A list is argv, where the number is the first bare digit run after the verb; a match
    object is the fallback regex, which captured it by name.
    """
    if isinstance(found, list):
        for token in found[3:]:
            if token.isdigit():
                return int(token)
        return 0
    return int(found.group("number") or 0)


def merge_target(tool_name: str, command: str, tool_input: dict[str, Any]) -> int | None:
    """The pull request number this call would merge, or None if it is not a merge.

    Zero means "a merge, but of a pull request we cannot name" — `gh pr merge` with no
    number merges whatever belongs to the current branch, which is the common shape.
    """
    if _MERGES_TOOL.search(tool_name):
        try:
            return int(tool_input.get("pullNumber") or 0)
        except (TypeError, ValueError):
            return 0
    found = _gh_subcommand(command, "merge", _MERGES_SHELL)
    if not found:
        return None
    return _number(found)


def head_of(tool_name: str, command: str, tool_input: dict[str, Any], cwd: str = "") -> str:
    """The branch the pull request is actually opened FROM, or "" when it cannot be told.

    The session's own branch was used before, and for the one session that coordinates —
    reading pull requests, merging, releasing, from the main checkout the worktree rule
    leaves it in — that is `main`. Every pull request it opened was filed as being ON the
    base branch, and "no commits on top of main" is then unsatisfiable rather than strict:
    a branch cannot gain commits over itself (#79).

    Three sources, in order of authority: the structured tool says `head` outright, `gh`
    accepts `--head`, and `cd <tree> && gh pr create` means the branch of that tree.
    """
    if _OPENS_TOOL.search(tool_name):
        return str(tool_input.get("head") or "")

    from . import shellcmd

    for argv in shellcmd.runs(command, "gh", "pr", "create"):
        for index, token in enumerate(argv):
            if token in ("--head", "-H") and index + 1 < len(argv):
                return argv[index + 1]
            if token.startswith("--head="):
                return token.split("=", 1)[1]
    return _branch_of(_directory_of(command) or cwd)


def _directory_of(command: str) -> str:
    """The directory a `cd` in this line moves to, if there is one."""
    from . import shellcmd

    for argv in shellcmd.commands(command):
        if argv and argv[0] == "cd" and len(argv) > 1:
            return argv[1]
    return ""


def _branch_of(directory: str) -> str:
    """The branch checked out in `directory`. Empty when it is not a working tree."""
    if not directory:
        return ""
    from .gitctx import _run

    try:
        return _run(["rev-parse", "--abbrev-ref", "HEAD"], directory, check=False).strip()
    except (OSError, ValueError):
        return ""


# `owner/repo` out of either URL spelling git writes. Anything else — a local path, a
# host this does not recognise — leaves the set empty, and an empty set vouches for
# nothing, which is the safe direction.
_REMOTE = re.compile(r"[:/](?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def _remote_names(ctx: GitContext) -> set[str]:
    from .gitctx import _run

    names: set[str] = set()
    try:
        listed = _run(["remote"], ctx.worktree_root, check=False).split()
    except (OSError, ValueError):
        return names
    for remote in listed[:8]:
        try:
            url = _run(["remote", "get-url", remote], ctx.worktree_root, check=False).strip()
        except (OSError, ValueError):
            continue
        found = _REMOTE.search(url)
        if found:
            names.add(f"{found['owner']}/{found['repo']}".lower())
    return names


def _repo_flag(command: str) -> str:
    """`--repo owner/name` out of a `gh` line, in either spelling."""
    from . import shellcmd

    for argv in shellcmd.commands(command):
        for index, token in enumerate(argv):
            if token in ("--repo", "-R") and index + 1 < len(argv):
                return argv[index + 1].lower()
            if token.startswith("--repo="):
                return token.split("=", 1)[1].lower()
    return ""


def _repository_named(tool_name: str, tool_input: dict[str, Any], command: str) -> str:
    """The repository this call names outright, or "" when it names none."""
    if not any(tool.search(tool_name) for tool in (_OPENS_TOOL, _MERGES_TOOL, _CLOSES_TOOL)):
        return _repo_flag(command)
    owner = str(tool_input.get("owner") or "").strip()
    repo = str(tool_input.get("repo") or "").strip()
    return f"{owner}/{repo}".lower() if owner and repo else ""


def about_this_repository(ctx: GitContext, tool_name: str, tool_input: dict[str, Any],
                          command: str = "") -> bool:
    """Is this pull request call about the repository the session is standing in?

    The obligation is recorded either way — a session that opened a pull request somewhere
    else still opened one, and the board should say so. What this decides is narrower and
    is only ever asked at the vouch: sparing the founder a prompt on a call that names
    somebody else's repository is outside every boundary this plugin publishes.

    Naming nothing is this repository, because that is what `gh` resolves a bare call to —
    the remote of the tree it is run in.
    """
    named = _repository_named(tool_name, tool_input, command)
    return not named or named in _remote_names(ctx)


def about_current_branch(tool_name: str, command: str) -> bool:
    """Does this merge unambiguously concern the branch that is checked out?

    `gh pr merge` with no number merges whatever pull request belongs to the current
    branch — so the checks below are about exactly the thing being merged, whether or not
    this plugin was installed when the pull request was opened. A numbered call is a
    different matter and is judged only against a recorded obligation.
    """
    if _MERGES_TOOL.search(tool_name):
        return False
    found = _gh_subcommand(command, "merge", _MERGES_SHELL)
    return bool(found is not None and not _number(found))


# How a merge is covered by the founder's word: as one of the pull requests a `+merge`
# named, or by the single merge a `+merge` allows when it named none.
POOLED, ONCE = "pooled", "once"


def _entry(record: dict[str, Any]) -> str:
    """One pull request, as a `+merge` names it: its branch and the moment it was opened.

    The moment as well as the branch, because a branch carries on after its pull request is
    merged and gets a NEW one — which nobody has looked at, and no earlier word covers.
    """
    return f"{record.get('branch')}@{float(record.get('opened_at') or 0):.3f}"


def _pool(ctx: GitContext) -> list[str]:
    return (config.asked_for(ctx, config.MERGE_POOL) or "").split()


def _the_chats(ctx: GitContext, session_id: str) -> Callable[[Any], bool]:
    """Whether a record's session is the chat this session is: the one the founder spoke in.

    The chat, not the process. A pull request records the identity that opened it, and
    `sessions.identities` unites identities only while they are one running process. A chat
    restarted with `--resume` is a new process with the same harness id, so everything it
    opened before the restart stopped being its own: `+merge` named only what it had opened
    since, took the one-merge word with it, and a merge of #804 was refused as unaccepted
    after every `+merge` the founder sent (#241). A `claude -p` it started carries that
    harness id too, and the founder speaks to one only through the chat that started it.
    """
    from . import hookio, sessions

    mine = sessions.identities(ctx, session_id)
    harness = hookio.harness_of(session_id, str(ctx.worktree_root))
    return lambda owner: owner in mine or hookio.composed_from(str(owner or ""), harness)


def accept_merges(ctx: GitContext, approvals: dict[str, str],
                  session_id: str) -> dict[str, str]:
    """The founder's word as it is recorded: a `+merge` names the pull requests it accepts.

    One `+merge` allowed one merge, so a session that had finished ten pull requests got
    the founder to say it ten times, in ten messages, about work they had already looked
    at together. Now it accepts every pull request the chat it was said to has open at
    that moment, each once.

    That chat's, not the clone's. The word is typed into one chat about the work in it;
    read across every session it would merge a sibling's pull request the founder had
    told that sibling to leave alone, which is #192 again with ten times the reach. And
    with none open it is what it always was — the one merge of the work just accepted,
    which the session opens, checks and merges by itself (decision 0010).

    Pull requests named by an earlier word and still open stay named. A draft is not named:
    it is work the founder paused, and a word about the finished ones is not a word about it.

    A pull request whose UNVERIFIED finish was put to the founder before this word is
    recorded as accepted with it, so that `blockers` stops listing what they have decided.
    """
    if config.APPROVE_MERGE not in approvals:
        return approvals
    ours = _the_chats(ctx, session_id)
    still_open = {_entry(record): record for record in outstanding(ctx)}
    named = {entry for entry, record in still_open.items()
             if ours(record.get("session_id")) and not record.get("draft")}
    if not named:
        return approvals
    for entry in sorted(named):
        _accept_its_finish(ctx, still_open[entry])
    kept = {entry for entry in _pool(ctx) if entry in still_open}
    rest = {key: value for key, value in approvals.items() if key != config.APPROVE_MERGE}
    return {**rest, config.MERGE_POOL: " ".join(sorted(kept | named))}


def named_by(ctx: GitContext, number: int, current: bool = False) -> dict[str, Any] | None:
    """The open pull request a merge names, whichever branch it is on. None when unknown.

    Not `gated_by`: that one answers "is this merge about the branch checked out here?",
    and a numbered merge of another branch is not. This answers "which pull request is
    it?", which is what a `+merge` that named several has to be asked.
    """
    records = _records(ctx)
    record = records.get(ctx.branch) if current or not number else records.get(_numbered(ctx, number))
    return record if record and record.get("state") == OPEN else None


def unplaced(ctx: GitContext, number: int, session_id: str) -> str:
    """The refusal for a merge by a number no record carries, when the word may cover it.
    "" when it does not apply.

    A `+merge` names the chat's open pull requests by their records, and a merge that gives
    a number is matched to a record by the number it learned. One opened with `gh pr create`
    before that number was read off its output never learned it, so `gh pr merge 48`
    matched nothing and was told no `+merge` was on record, while one was, for that very
    pull request. The session asked the founder for the word four times (#241). This says
    what is true, and names the merge that matches by branch instead.
    """
    if number <= 0 or _numbered(ctx, number):
        return ""
    ours = _the_chats(ctx, session_id)
    pooled = set(_pool(ctx))
    blind = sorted({str(record.get("branch")) for record in outstanding(ctx)
                    if ours(record.get("session_id")) and _entry(record) in pooled
                    and not _as_number(record.get("number"))})
    if not blind:
        return ""
    return (
        f"claude-bestpractice: #{number} is not a pull request this clone knows by number. The "
        f"founder's `+merge` is on record for this chat's pull request on {', '.join(blind[:3])}, "
        "and that one's number was never learned here.\n"
        f"If #{number} is it, merge it without the number, from the tree that has its branch "
        "checked out, where it is matched by the branch:\n"
        "  gh pr merge --squash\n"
        "Do not ask the founder for `+merge` again: their word is already on record."
    )


def acceptance(ctx: GitContext, record: dict[str, Any] | None) -> str:
    """How the founder's word covers merging `record`: `POOLED`, `ONCE`, or "" when it does not."""
    if record is not None and _entry(record) in _pool(ctx):
        return POOLED
    return ONCE if config.approved(ctx, config.APPROVE_MERGE) else ""


def spend(ctx: GitContext, record: dict[str, Any] | None, cover: str) -> None:
    """Take this merge off what the founder's word covers. Each pull request merges once."""
    if cover == ONCE:
        config.clear_switch(ctx, config.APPROVE_MERGE)
        return
    if cover != POOLED or record is None:
        return
    left = [entry for entry in _pool(ctx) if entry != _entry(record)]
    if left:
        config.record_switches(ctx, {config.MERGE_POOL: " ".join(left)})
    else:
        config.clear_switch(ctx, config.MERGE_POOL)


def gated_by(ctx: GitContext, number: int, current: bool = False) -> dict[str, Any] | None:
    """The obligation this merge is judged against, or None to let it through.

    Judged only when the answer would be about the right branch. Every check available
    here reads the CURRENT working tree, so refusing a merge of somebody else's branch on
    the strength of what is checked out now would be a refusal with a reason that is not
    true of the thing refused — which costs a detour and then costs trust.

    A pull request opened before this plugin was installed, or from the website, has no
    obligation on record and is invisible to everything else in this module. It is still
    judged when the call names the checked-out branch, because that is the one case where
    the local checks are known to be about it.
    """
    record = _records(ctx).get(ctx.branch)
    if not record or record.get("state") != OPEN:
        return {"branch": ctx.branch, "base": ""} if current else None
    if not number:
        return record
    # A NAMED number is this branch's only when the record can confirm it. Treating
    # "cannot tell" as "ours" is what refused an unrelated merge with this branch's
    # problems, in a session whose real work was somewhere else entirely (#135). Every
    # record used to carry number 0, because `opened` runs in PreToolUse and never sees
    # the response that contains the number; `learn_number` is what fills it in now.
    return record if number == int(record.get("number") or 0) else None
