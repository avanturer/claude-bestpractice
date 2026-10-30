---
title: A listing of agents says what each session on the repository is doing
paths: plugin/hooks/bestpractice.ts, plugin/lib/claude_bestpractice/board.py, plugin/bin/claude-bp-mod
date: 2026-10-30
---

## Decision
**When a session lists the agents it can message, each other live session on the repository is
named there with its card, branch and tree**, read fresh, names included. The board says that
`SendMessage` reaches a session by the name its row starts with.

## Why
> бесит что есть доска задач но что бы написать кому то смс он перебирает всех активных агентов

The board names sessions once, when a session starts (decision 0003). A session started later
was a bare name in `ListAgents`, and one with something to say asked them all. Each message is a
turn the recipient pays for.

## Rejected
- **Injecting the board again on every prompt.** Quadratic in the turns, for the moment a
  message is sent.
- **A command that answers "who holds this card".** A session reaches for `ListAgents`, not for
  a command it has to know of.
- **Naming every session after its card** (`sessionTitle`). The founder's names are theirs.

## Cost accepted
A listing costs a third of a second more, for `claude agents --json`.
