---
title: A pull request is filed under its head on GitHub, and a branch gone from the clone settles nothing
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool, plugin/bin/pr-opened, plugin/lib/claude_bestpractice/migrate.py
date: 2026-11-12
---

## Decision
**A pull request is filed under the head GitHub opens it on**: the one the call names, else the
remote branch the local one is pushed as and stands level with (`@{push}`, then its upstream),
never the trunk or the base. The local branch it was opened from is kept beside it (`local`),
and every gate that asks about the branch a tree has checked out finds it under either name.

**A branch gone from this clone settles nothing GitHub may still have open.** Gone under both
names, the record leaves the board as before, marked `branch gone`, and a `+merge` names its
number as it names any open one's: in the message it answers, or put to the founder by a refusal.
Only GitHub's own word (a merge or close a session ran, the trunk naming the number) or the
trunk holding the work settles it. Records an older version closed that way are marked by repair
0049. This revises decision 0043's "recorded as merged or closed" for such a record.

## Why
> Гейт +merge считает открытый PR #1135 закрытым на временной ветке tmp/assistant-all: ветка была
> слита локально и удалена, а PR живёт на fix/diet-hidden-animals.

`git push -u origin tmp/assistant-all:fix/diet-hidden-animals` and `gh pr create` filed #1135
under the local name. Deleting it closed the record, and every `+merge` after skipped #1135 as
"recorded here as closed" (#325).

## Rejected
- **Asking GitHub at every reconcile.** A network call per record, from the Stop gate.
- **Keeping a gone record open.** Decision 0056's reason stands: it was on every board for a
  month as ready to merge, and nothing cleared it.

## Cost accepted
A pull request closed on GitHub whose branch went from the clone can be named by a word again,
and its merge is then GitHub's to refuse. A branch pushed under another name and ahead of it
when the pull request is opened is filed under its own name, as `gh` pushes it.
