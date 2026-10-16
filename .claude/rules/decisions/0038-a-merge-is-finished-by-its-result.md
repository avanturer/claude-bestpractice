---
title: A merge is finished by its result, not by its request
paths: plugin/bin/pre-tool, plugin/bin/pr-opened, plugin/lib/claude_bestpractice/pullrequest.py, plugin/hooks/hooks.json
date: 2026-10-16
---

## Decision
**The merge gate judges a merge before it runs and finishes it after.** PreToolUse still decides
whether it may go; what follows from it having gone waits for the PostToolUse of the same call,
matched by `tool_use_id`: the founder's word spent, the pull request settled, the cards it carried
closed, the other sessions told. A merge that fails has done none of that, and its retry is still
covered by the word.

**The one merge a word allows when it named nothing is moved onto the pull request it was used
on**, so a retry is covered and no other pull request is while it is in flight.

**`gh pr merge --auto` spends the word and settles nothing.** The pull request stays open until
its work is on the trunk, where `reconcile` settles it.

## Why
> Spent at PreToolUse, a conflict-failed merge costs the founder a second `+merge`.

Measured in a live session: the merge failed on a conflict, the record said MERGED, the card had
closed and the word was gone. #232, #241, #243, #246 and #268 each fixed one way the word went
missing before the merge; this is the one they all share. PostToolUse fires only for a call that
succeeded, and carries the same `tool_use_id` as its PreToolUse, measured on Claude Code 2.1.301.

## Rejected
- **Spending at PreToolUse and restoring on failure.** PostToolUseFailure would be a thirteenth
  hook entry, and a call cut off before either event would still lose the word.
- **Asking GitHub after the call whether it merged.** A network round trip on a tool call, for
  what the harness already says.
- **Leaving the single merge unspent until the result.** Two merges sent in one batch would both
  pass on one word.

## Cost accepted
A merge that succeeded but whose PostToolUse never ran leaves the pull request open until the Stop
gate's `reconcile` sees its work on the trunk, and its cards until the Stop gate asks. A merge
queue's `gh pr merge` is still recorded as merged when it only queued. Decision 0011's early
closure of a card no longer happens.
