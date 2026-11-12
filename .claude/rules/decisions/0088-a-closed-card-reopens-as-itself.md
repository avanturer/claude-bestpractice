---
title: A closed card reopens as itself, and every state a card stands in has a way on
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/claude-bp-plan, plugin/bin/evidence-gate
date: 2026-11-12
---

## Decision
**`claude-bp-plan reopen` puts a done or withdrawn card back in `next` as the same card**: its
files, condition, order and history kept, a line saying it was reopened, every copy in every tree
moved under the claim lock. A live sibling's card is its own to reopen. Whoever holds a card
`--after` it is told it has not landed after all.

**A delivery of what a reopened card's files held when it was reopened neither closes it nor is
owed its close.** The reopen writes a fingerprint of those files per place their work could be
read back from: this tree's HEAD, the trunk as fetched and as held, the card's branch. The merge,
the Stop gate and repair 0011 pass over a card whose files at HEAD match one. Closed, it has none.

**Every refusal about a card's state names the command that moves it on from there**, and the
tests run each one from every state. **A blocker lives only on a paused card**; repair 0047 takes
it off the rest. **A body past its cap loses its middle**, never the history appended to its end.

## Why
> A way to reopen a done task — for example `resume` accepting a done task, or a `reopen`
> command — so the same task, with its history and paths, goes back to the queue.

`done` was the one state no command left: `resume` said "not paused", `claim` "already done",
and the rest of the work went on a second card, its history split and every pointer to the first
redone by hand (#307). Reopened, it would have closed again at the end of the same turn: the
Stop gate settles a session's cards whose files reached the trunk, and they had, before it.

## Rejected
- **`resume` or `claim` taking a done card.** A pause and a closure say different things, and a
  stale number would bring finished work back unasked.
- **Reopening into `doing` for the session asking.** The queue is what was asked for.
- **Commits for the fingerprint.** A squash merge is a commit no reopen saw; its content is not.

## Cost accepted
Another session's merge of a reopened card's files after the reopen reads as new work, and its
delivery closes the card. A card whose files are amended after the reopen is judged as one never
reopened. A `done` copy a branch still tracks comes back with that branch and outranks it.
