---
title: Sessions talk through the inbox a hook writes
paths: plugin/lib/claude_bestpractice/inbox.py, plugin/bin/pre-tool, plugin/bin/evidence-gate
date: 2026-08-23
supersedes: 
---

## Decision
**A session tells its siblings by writing their inbox, and a hook does the writing.** The
address and token Claude Code exports to hooks are used directly to build the frame the
receiver reads; nothing here calls the harness's messaging tool, because a hook cannot
call a tool.

**A fact is delivered, a question is owed an answer.** `inbox.post` queues a claim about
the repository, deduplicated on the claim itself; `inbox.ask` queues a question, and the
recipient's Stop gate refuses to end a turn while one is unanswered. The difference is
structural, or it is a fact with a question mark on it.

**Only facts about the repository travel.** A lease refusal to the path's owner, a
baseline moved by somebody's merge, a red suite on a path a sibling holds, a card that
unblocked them, two sessions heading into one file from different trees. Nothing else.

## Why
> полностью ли наш плагин и правильно делает общение соседних чатов которые в работе и тд, у клода же ест ьфункция сообщений между чатам, мы используем её строго обязательно и по полной?

The answer at the time was "not fully", and the reason mattered: the board is injected
once at session start (decision 0003), so a running session could not be told anything.
Everything a sibling needed to know had to wait for a restart — measured against an
eleven-hour session still showing an eleven-hour-old account percentage.

## Rejected
- **`SendMessage`, the harness's own tool**: a hook is not a model turn and cannot call a
  tool. The frame is written directly instead, and a doctor check binds a real `AF_UNIX`
  socket and proves it arrives — so an undocumented format that changes turns a gate red
  rather than killing the feature in silence.
- **Injecting the board every turn**: O(T²) against O(T). Measured in base-input
  equivalents for a 1,000-token block: 6,000 once against 164,000 per-turn at 41 turns.
- **Two-way dialogue between sessions**: that is agent chat, not facts about the
  repository, and this plugin holds the pen on the second thing only (decision 0008).
- **Waking an idle sibling** (`notify_when_idle`, 2.1.236): deferred, not refused. The
  channel delivers on the recipient's next tool call, which is enough for a session that
  is working and nothing for one that is not.
