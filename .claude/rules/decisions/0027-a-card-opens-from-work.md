---
title: A card opens from work, and one no work followed is withdrawn
paths: plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/statements.py, plugin/bin/prompt-capture, plugin/bin/evidence-gate
date: 2026-10-01
---

## Decision
**A founder's message opens a card only when it states work and is not a question.** A
session's first message is still recorded as what it was told, and opens nothing on its own.
Claude Code's plain-text idle notice is the harness speaking, not the founder.

**The next message renames that card only while nobody has written on it.** The session names
its own card with `claude-bp-plan update --title`.

**A card no work followed is withdrawn when its turn ends.** If the turn planned nothing on it
(no paths, no condition, no note, no owner), the Stop that allows the finish moves it to
`withdrawn/`: off the board, still on disk, and its number never handed out again.

## Why
> Вопросы, подтверждения и служебные сообщения задач не порождают. Если сессия всё же завела
> карточку из реплики, а работы по ней не было, карточка не висит в `next` неделями.

Twenty-one of one board's 156 open cards were «статус», «тебя можно закрывать?», «не в тот чат
отправил» and an idle notice, and a real task had been renamed «тебя можно закрывать?» (#256).
Not opening them loses nothing: the first write still demands a card, named by the session
doing the work.

## Rejected
- **Deleting such a card.** A session may have been told its number, and the file is the undo.
- **Parking it in `paused`.** Paused waits on something nameable; this waits on nothing, and
  would sit on the board the way `next` did.
- **Asking the founder which of them are work.** The board already answers: work plans its card.

## Cost accepted
Work asked as a question ("can you fix the login?") has no card until its first write demands
one. Repair 0039 withdraws the untouched cards of sessions that are no longer live.
