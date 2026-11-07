---
title: A turn that waits on its own agents is not a finish
paths: plugin/bin/evidence-gate
date: 2026-11-06
---

## Decision
**A Stop whose `background_tasks` names an agent of this session still running is not judged
as a finish**: a subagent, a workflow or a cloud session whose status says the work goes on, and
whose report wakes the session again. No suite runs, nothing unfinished is refused, nothing is filed UNVERIFIED and no
pull request is demanded or handed off; the card a message opened, the leases and the tree stay.
What the others are owed still holds, as for a pause (decision 0033), and the finish after the
reports is judged in full, a card closed in between included.

**A background shell, a monitor or a teammate spares nothing.** The first two may never end, and
Claude Code keeps an idle teammate's entry `running` for good (anthropics/claude-code#85955).

## Why
> изучи что такое самый новые deepseek harness о котором говорят, самые современные статьи и
> плагины для клод кода vscode, что бы взять все лучшее и исправить неполноценное или
> неоптимальное к нам в плагин

Claude Code runs a spawned agent in the background by default since 2.1.232, and Stop has carried
`background_tasks` since 2.1.145 to tell "session is done" from "session is paused waiting for
background work". The gate never read it. A session that handed work to three agents and ended
its turn to wait had the suite run over files they were still writing, the card it was opened
with withdrawn as unplanned, and the tree of a merged branch removed from under them.

## Rejected
- **Every background task.** `npm run dev` left running would spare every finish for good.
- **`session_crons`.** A scheduled check is a later turn, not work still being done in this one.
- **Deferring the whole gate.** Drift, a sibling's question and the card are owed now, while the
  agents write.

## Cost accepted
A session can keep its finish from being judged for as long as it keeps an agent running. A merge
still needs a green run, and the first finish with no agent at work is judged.
