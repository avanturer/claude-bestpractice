---
title: A delivery closes a card only when the chat carries nothing else under it
paths: plugin/lib/claude_bestpractice/plan.py
date: 2026-11-11
---

## Decision
**A merge, or a finish over delivered work, closes a card whose files it carried only where
nothing else of the chat's touches that card's files and is not in the trunk**: another open
pull request of the chat that is not a draft, or another tree of the chat with uncommitted
work or commits the trunk lacks. Otherwise the card stays in `doing` and is written what was
delivered and what it waits on. Decision 0011 holds for the delivery after which nothing is left.

**A session record stamped on an earlier boot of this machine is dead**: the same host name,
another boot id.

## Why
> нужно что бы Я в репозитории фудди обновил плагин и все там стало актуальным без багов и
> починилось

A founder's card over one piece of work, 170 files shipped as a dozen pull requests, closed on
the first merge, which carried six of them. Each write under it after that was refused as work
no card claimed, a subagent's in its own tree among them; the card filed to continue it closed
the same way an hour later. After a WSL restart the chat's own record of before the restart
held the card for 36 hours: WSL gives each start a new pid namespace, and a pid in another
namespace is "cannot tell".

## Rejected
- **All of a card's files, not any.** Decision 0011's reason stands: almost nothing closes.
- **Every open pull request of the chat.** One under another card would hold this card open
  with nothing to close it later.

## Cost accepted
A card stays in `doing` while a tree of the chat holds a stray change to one of its files,
until that tree is clean or `sweep_idle` returns the card. Another machine with this host's
name sharing the clone would have its records read as dead.
