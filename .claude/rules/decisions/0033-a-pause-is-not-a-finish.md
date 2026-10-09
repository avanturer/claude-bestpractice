---
title: A pause is not a finish
paths: plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-10-09
---

## Decision
**A turn that ends on a pause is not asked for what only a finish owes.** It ends on one when
the session holds nothing in `doing`, has closed no card since its last turn ended, and wrote
nothing in the diff after the last card it paused. Then no suite runs, on the tree or on the
commit, unfinished work is not refused, nothing is filed UNVERIFIED, and the board is not asked
for a card: the paused card says what the work is and what it waits on.

**What the others are owed still holds**: drift, with the paused card's files declared, a
question from another session, the dependency comparison, a refusal nothing answered. The suite
is asked again when the card is claimed and the work finished, and a merge needs a green run.

**A pause writes who set the card down** (`paused_by`), since it clears the owner. A refusal
only a finish owes names the pause, for the founder who asked to stop.

## Why
> Задача на паузе с записанным блокером (словом пользователя) — законный конец хода: гейт не
> требует зелёного набора и не просит снова взять задачу.

Told to stop until tomorrow over tests left red on purpose, a session was refused twice for the
red suite, paused its card, and was refused a third time: "nothing on the board says this
session is working", with `claim` named to take the card back (#264).

## Rejected
- **A `+pause` from the founder.** A pause claims nothing done and reaches no merge without a
  green run; the founder's word guards accepting work, not stopping it.
- **Keeping the owner on a paused card.** A pause hands the work back, and a card held by a
  live session is refused to whoever picks it up.
- **Any card the session ever paused.** A card closed in the same turn is a finish, and a write
  after the pause is work no card covers.

## Cost accepted
A session can stop over a red suite by pausing its card, and the board says so in the blocker it
wrote. Repair 0040 names who paused an older card only where one live session works on its
branch; any other is paused again to say.
