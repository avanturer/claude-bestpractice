---
title: A prompt a session schedules, or a message it sends, carries no word of the founder's
paths: plugin/lib/claude_bestpractice/founder.py, plugin/bin/pre-tool, plugin/hooks/hooks.json
date: 2026-11-06
---

## Decision
**`CronCreate`, `ScheduleWakeup` and `SendMessage` are refused when their text would read as the
founder's `+merge`, `+release` or `+migration`, or as a gate switch**, judged as `prompt-capture`
judges a message: a word in backticks, asked about or withheld is no word, and goes through.
The refusal names that way of saying it.

## Why
> изучи что такое самый новые deepseek harness о котором говорят, самые современные статьи и
> плагины для клод кода vscode, что бы взять все лучшее и исправить неполноценное или
> неоптимальное к нам в плагин

Claude Code fires UserPromptSubmit for a scheduled task, a `/loop` iteration and a message from
another session, and documents no field telling such a turn from one the founder typed;
`prompt-capture` records grants and switches from exactly that event. Whether the text arrives
wrapped, where the founder's reader would skip it, is not documented either. A session could
schedule `+merge` a minute out and merge on it. Claude Code closed the same road for itself in
2.1.183: a scheduled delivery is no longer keyboard input and cannot approve a pending action.

## Rejected
- **Telling such turns apart in `prompt-capture`.** Nothing in the event says which they are.
- **Refusing every scheduled prompt.** `/loop` is how a session watches CI.
- **Any text with a `+` in it.** "the founder's `+merge`" names the word without giving it.

## Cost accepted
A session that tells a sibling what the founder said writes the word in backticks. A `/loop`
the founder types with a grant in it is refused, as decision 0023 refuses a grant on a timer.
