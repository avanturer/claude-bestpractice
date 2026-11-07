---
title: An agent is told the card it works under
paths: plugin/bin/subagent-brief
date: 2026-11-06
---

## Decision
**A subagent's brief names the cards the session that started it holds**, two at most, as
facts: the card's id and title, when it is done, and its files, beyond which a write counts as
drift at the session's finish. A session that holds no card hands none.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

An agent was handed a task and nothing of the work it was part of, and the files the session's
finish measures drift against were invisible to it. Claude Code runs the main conversation's
subagents in the background in VS Code since 2.1.302, so more of the writing is theirs.
DeepSeek Harness gives a worker its ticket's scope, and takes it away when the ticket ends.

## Rejected
- **Refusing an agent's write beyond the card.** The session's finish already judges drift,
  and an agent cannot amend the card it was not handed.
- **The card's body.** It is the session's notes; the files and the condition are the scope.

## Cost accepted
Up to eight hundred characters more on every spawn of a session that holds a card.
