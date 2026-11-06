---
title: The founder sees the board beside the chat, and the model is never sent it
paths: plugin/hooks/bestpractice.ts, plugin/lib/claude_bestpractice/board.py, plugin/bin/claude-bp-mod
date: 2026-11-06
---

## Decision
**The hooks module keeps a status line under the prompt** — how many other sessions are live,
which pull requests wait for the founder's `+merge`, how many are blocked, whether the suite is
red — **and `/sessions` opens a pane** with a row for each session (its card and branch), each
pull request and the red suite. Read again at the end of every turn and once a minute; drawn in
the terminal and in VS Code; never part of a request, so it costs no tokens. Sessions are live
by pid, as the reaper reads them. `enabled off`, or a directory outside every repository,
clears the line.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

The board reached only the model, once per session start (decision 0003), and a founder with
three to eight sessions open asked each one what it was doing, which is a turn each. `DESIGN.md`
§8 asked for state rendered through the status line at zero context tokens, and the founder
works in the VS Code extension, which runs no custom `statusLine` command; a mod's status line
and pane are drawn there since its 2.1.299.

## Rejected
- **A slash command that prints the board.** Its output is a turn's text the model then reads.
- **`claude agents --json` for the live set.** A third of a second, every minute, per session.
- **The pane opened unasked.** Unasked, Claude Code places a pane only from 144 columns.

## Cost accepted
A Python process per session every minute and at every turn's end. A session started before
this version shows nothing until it is restarted.
