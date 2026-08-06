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
already on the board. Nothing calls the network: this runs inside a PreToolUse hook, and
a gate that costs a round trip on every tool call is a gate that gets switched off.
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import store
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
           number: int = 0, url: str = "") -> None:
    """Record that this branch now has a pull request waiting on it.

    Recorded when the call is ALLOWED to proceed rather than after it returns, because a
    PreToolUse hook never sees the result. An obligation for a call that then failed is
    the cost of that, and it is a bounded one: the Stop gate hands it to the founder once
    and never blocks on it again.
    """
    existing = _records(ctx).get(branch, {})
    if existing.get("state") == OPEN:
        return
    _write(ctx, {
        "branch": branch,
        "base": base,
        "number": number or int(existing.get("number") or 0),
        "url": url,
        "session_id": session_id,
        "opened_at": time.time(),
        "state": OPEN,
        "handed_off_at": 0.0,
    })


def settle(ctx: GitContext, branch: str, state: str) -> None:
    """Discharge the obligation — merged, or closed without merging."""
    record = _records(ctx).get(branch)
    if record and record.get("state") == OPEN:
        _write(ctx, {**record, "state": state, "settled_at": time.time()})


def outstanding(ctx: GitContext) -> list[dict[str, Any]]:
    """Every pull request this clone knows about and has not seen the end of."""
    now = time.time()
    live = [
        row for row in _records(ctx).values()
        if row.get("state") == OPEN and now - float(row.get("opened_at") or 0) < MAX_AGE_SECONDS
    ]
    live.sort(key=lambda r: float(r.get("opened_at") or 0), reverse=True)
    return live


def unhanded(ctx: GitContext, branch: str) -> dict[str, Any] | None:
    """This branch's open pull request, if the founder has not been told about it yet."""
    record = _records(ctx).get(branch)
    if record and record.get("state") == OPEN and not record.get("handed_off_at"):
        return record
    return None


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
    from . import board, delivery, drafts, provenance

    branch = head or ctx.branch
    problems = (
        list(delivery.ready(ctx, base)) if branch == ctx.branch
        else _about_the_pull_request(ctx, base, branch)
    )
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
        if _all_dismissed(ctx, str(item.get("text", "")), subjects):
            continue
        problems.append(str(item.get("text", ""))[:200])
    return problems


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
    entry = evidence.red(ctx)
    if entry and entry.get("branch") == head:
        problems.append("the test suite is red")
    if not evidence.last_green(ctx, head):
        problems.append(f"no test run has ever been observed on {head}")
    if delivery.unverified_on(ctx, head):
        problems.append(f"{head} carries an UNVERIFIED finish")
    return problems


def _files_against(ctx: GitContext, base: str, head: str = "HEAD") -> set[str] | None:
    """Paths this branch changes relative to its merge base with `base`.

    None when git cannot answer — an unknown base, an unborn branch — and the caller then
    keeps every finding. Losing a real finding is worse than repeating a stale one, so the
    filter only ever narrows on an answer it actually got.
    """
    from .gitctx import _run

    for ref in (base, f"origin/{base}"):
        listed = _run(["diff", "--name-only", f"{ref}...{head or 'HEAD'}"], ctx.worktree_root, check=False)
        if listed.strip():
            return {line.strip() for line in listed.splitlines() if line.strip()}
    return None


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
        "empty."
    )


def stop_demand(record: dict[str, Any], problems: list[str]) -> str:
    """The one interruption a pull request gets: merge it, or say why it cannot be."""
    named = f"#{record['number']}" if record.get("number") else f"on {record.get('branch', '')}"
    if not problems:
        return (
            f"claude-bestpractice: pull request {named} is open and passes every check, and this "
            "turn was about to end without it being merged.\n"
            "Merge it now. There is no reviewer and no approval step in this repository — a "
            "pull request left open is work that is finished everywhere except where it "
            "counts.\n"
            "If you believe it genuinely must not be merged yet, say so to the founder in "
            "one line; this will not be raised again."
        )
    listed = "\n".join(f"  - {p}" for p in problems[:6])
    return (
        f"claude-bestpractice: pull request {named} is open and cannot be merged — the final "
        f"check found {len(problems)} thing(s):\n{listed}\n"
        "Report exactly this to the founder and stop. Do NOT push changes to make the check "
        "pass — how to resolve these is their decision, because the fixes that make a branch "
        "green at merge time are often ones nobody wanted.\n"
        "This will not be raised again; it is now on the board until the pull request is "
        "merged or closed."
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
    if number and int(record.get("number") or 0) and number != int(record["number"]):
        return None
    return record
