---
title: A chat is one session wherever its own process acts
paths: plugin/lib/claude_bestpractice/sessions.py, plugin/lib/claude_bestpractice/gitpolicy.py, plugin/lib/claude_bestpractice/plan.py, plugin/bin/pre-tool, plugin/bin/prompt-capture, plugin/bin/evidence-gate
date: 2026-11-12
---

## Decision
**`sessions.identities` is the one answer to "is that record this session's"**, read by every
gate asking whose tree, whose files, whose card. A session is a chat in one CLI, by pid and start
time, in whichever tree; what that CLI left under an earlier conversation id, by `/clear` or
`/resume`, is its past and never a sibling's; where no pid was resolved, the main checkout and
every tree this plugin made for the chat hold one session for its cards, never for its tree.

**A hook writing its session's record stamps its own CLI onto one that is the chat's past**,
judged before the write: the tool gate, the founder's message and the finish alike. The old CLI
writing for itself keeps its record and is live again. This revises decision 0091, under which
the resumed chat's own writes counted as the old process writing again.

**A refusal over work no card of this session covers names `claude-bp-plan claim <id>`** where
this chat holds a card naming the path under another id and `claim` would hand it over.

## Why
> `git worktree remove --force <its tree>` from the main checkout → refused: "fuddy-df
> (c7775792) [another process of this chat …] works there". c7775792 is this very session.

Resumed and back in its tree, the chat wrote the old CLI's record as that CLI's (#312, #319);
its agent was refused the files of the card it was handed (#320, #321); after `/clear`,
`git add -A` was refused over its own files (#361). Three readers, three answers.

## Rejected
- **One harness id as one session.** A `claude -p` the chat starts shares it (decision 0032).
- **Stamping on a subagent's `agent_id`.** An orphaned CLI's agent at work carries one too.
- **A `/clear`ed conversation's cards as the new one's.** Decision 0028 hands them back.

## Cost accepted
An old CLI that writes in a tree after the chat went back into it is recorded as the chat.
Repair 0050 hands a chat back each record an older version wrote for it as its old CLI's.
