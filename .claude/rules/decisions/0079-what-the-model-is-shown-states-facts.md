---
title: What the plugin puts in front of the model states facts, never orders
paths: plugin/lib/claude_bestpractice/board.py
date: 2026-11-06
---

## Decision
**Text this plugin injects as context is written as facts about the repository**, never as an
instruction: the board's header says that files another session holds are refused to every
other session, where it said not to edit them. Refusals are a different channel and keep naming
the command that answers them (decision 0020).

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

Claude Code's hooks reference, on `additionalContext`: "Write the text as factual statements
rather than imperative system instructions… Text framed as out-of-band system commands can
trigger Claude's prompt-injection defenses, which causes Claude to surface the text to you
instead of treating it as context." The board is fenced as data that is never to be treated as
instructions, and carried one.

## Rejected
- **Dropping the fence.** It is what keeps a sibling's task text from reading as an order.

## Cost accepted
None measured: the gate refuses the same writes whatever the board says about them.
