---
title: The compaction summary is told what to keep by the hook that saves the window
paths: plugin/bin/checkpoint, plugin/lib/claude_bestpractice/hookio.py, plugin/hooks/bestpractice.ts
date: 2026-11-06
---

## Decision
**The checkpoint prints what the compaction summary keeps, as plain text**, which Claude Code
uses as the summary's instructions: the founder's words and every `+merge` with what it covered,
the cards in play, the failing tests, and this session's claimed cards by id, title and
condition. Under two thousand characters; Claude Code cuts at ten thousand. The hooks module no
longer says it.

## Why
> изучи что такое самый новые deepseek harness о котором говорят, самые современные статьи и
> плагины для клод кода vscode, что бы взять все лучшее и исправить неполноценное или
> неоптимальное к нам в плагин

The hook printed `{"continue": true, "suppressOutput": true}`, fields this event discards, so it
told the summary nothing; what to keep reached it only through the hooks module's
`session.compact`, which is off wherever mods are. And what the next session start puts back is
read beside a summary that has already decided what the work was: a card the summary never named
is the one a session goes on to file a second time. DeepSeek Harness writes its compaction the
same way, to fixed sections that carry the request, the errors and the work in flight.

## Rejected
- **Both the module and the hook.** The summary would be told the same thing twice.
- **The whole checkpoint as instructions.** It is the restore's to hand back; the summary needs
  to be told what to keep, not given a second copy of it.

## Cost accepted
Up to two thousand characters more in each compaction request.
