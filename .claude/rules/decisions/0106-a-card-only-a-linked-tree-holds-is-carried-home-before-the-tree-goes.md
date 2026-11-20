---
title: A card only a linked tree holds is carried home before the tree goes
paths: plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/migrate.py, plugin/bin/pre-tool
date: 2026-11-20
---

## Decision
**A card no other tree has goes to the main checkout before its tree is removed**
(`plan.carry_home`, under the claim lock): in `_release` before every removal this plugin makes,
in `pre-tool` before a `git worktree remove` a session types, and for every linked tree once
(repair 0051). Only a card git does not track there and whose number no card in the main
checkout holds; the same card is told by `created_at`, or by title on a card older than it.

**A card whose number a different card there holds is never moved or deleted by this plugin**:
`lost_with` names it, so the reaper, the sweep, an agent's tree and the Stop gate keep its tree,
and a typed removal is refused naming `cat <card>`. This revises decision 0039's exemption of
this plugin's own files for such a card, and repair 0012's "each tree carries its own".

## Why
> Do this task here: "Keep a card stranded in a linked tree from going with it"

`lost_with` exempted everything under `.claude/claude-bestpractice/`, the ledger folder is
ignored in every tree (decision 0018), and `git worktree remove` takes an ignored file without a
word. Repair 0012 carried cards home from the tree it ran in, and a repair's record is the
clone's, so it ran in one tree per clone. Before 2.19.3 a dead listing wrote cards there too.

## Rejected
- **Renumbering a card whose number is taken.** A session may have been told it (decision 0027).
- **Keeping every tree that holds a card.** A copy every transition keeps in step is not lost.
- **Carrying a card git tracks.** The branch keeps it, and moved it keeps the tree (#208).
- **`ExitWorktree` in `pre-tool`'s matcher.** A process per exit, for cards 0051 has carried.

## Cost accepted
A tree removed by `ExitWorktree`, or from a terminal, takes a card that reached it after repair
0051 ran. A card whose number is taken keeps its tree until it is filed again by hand.
