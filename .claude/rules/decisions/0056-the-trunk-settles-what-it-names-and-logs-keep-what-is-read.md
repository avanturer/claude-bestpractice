---
title: The trunk settles a pull request it names, and a log keeps what is read
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/board.py, plugin/lib/claude_bestpractice/store.py, plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/plan.py
date: 2026-10-30
---

## Decision
**An open pull request whose number the trunk's history names as merged is settled as merged**:
a subject ending in `(#N)`, as GitHub writes a squash, or starting `Merge pull request #N from`.
Read with one `git log` of the fetched trunk, wherever `reconcile` runs.

**A log the plugin appends to is cut down to what can still be read once it passes a size**:
the pull-request log to the latest row per branch, without open rows past the thirty days an
obligation lasts or settled ones past ninety; the open items to the ones the board can show,
none about a branch the clone no longer has. Rewritten under a lock every writer appends under.
A branch's green and run records go with the branch. The board reads a card only once the
listing says it is the copy that answers, in the state asked for.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

On a founder's clone sixteen of eighteen open records were pull requests merged up to ten days
before, offered on every board as ready to merge. The logs held 1,016 rows for 281 branches and
1,298 for 848 items, three in four about deleted branches, and every gated write read 700 cards
to learn what one session held.

## Rejected
- **Asking GitHub.** A network call per record, from hooks that run on every tool call.
- **Rewriting without a lock.** A row appended between the read and the rename is lost, and in
  the pull-request log that row is an obligation.
- **Archiving finished cards.** Every reader of `done/` would need a second place to look.

## Cost accepted
A commit cherry-picked onto the trunk under a squash subject settles the number it carries. A
record that never learned its number is settled only by the branch going or its work landing.
