---
title: The founder's answer in a dialog is their word, and only their answer
paths: plugin/lib/claude_bestpractice/founder.py, plugin/bin/pr-opened, plugin/bin/pre-tool, plugin/hooks/hooks.json
date: 2026-11-12
---

## Decision
**`+merge`, `+release`, `+deploy` or `+migration` the founder gives by answering an
`AskUserQuestion` dialog is their word, exactly as if typed**, recorded where a typed one is and
naming what a typed one names, the pull requests the questions name by number among them. Text
they typed into the dialog (under "Other", as a reply, as a note) is read as a message of theirs
(decision 0047). An option they picked counts only where its label is the word and nothing else.
A question, an option nobody picked, and a dialog that closed on its own while they were away
count for nothing. Heard by `pr-opened`, on the PostToolUse entry it already has.

**A dialog's call that already carries `answers`, `response` or `annotations` is refused at
PreToolUse**: the answers are the founder's to give in the dialog. A call that asks without them
is noted by its id, and no answer of a dialog not noted counts.

This revises decision 0010: the word is read from the founder's own message, or from their own
answer to a dialog.

## Why
> Гейт +merge не видит ответ в диалоге AskUserQuestion: основатель выбрал вариант «+merge», но
> запись считается только из обычного сообщения.

No hook heard the pick: it starts no turn, and the transcript files it as a tool result, which
the reader of their words skips (#326). Read in Claude Code 2.1.322: the dialog writes the answer
into the call's input before the tool runs, so PostToolUse's input holds it whoever wrote it, and
only PreToolUse sees the input the session sent.

## Rejected
- **A thirteenth hook entry.** The budget is twelve; both gates already run on these events.
- **Any label holding the word.** «Show the diff, then +merge» picked for the diff would merge.
- **Reading the dialog out of the transcript.** Its text quotes what the session wrote, unmarked.

## Cost accepted
A hook or a `claude -p` permission host set up outside the session's own call answers a dialog as
the founder: Claude Code's result does not say who picked. A label that explains itself beside
the word is not read; the explanation goes in the option's description. A dialog answered where
`pre-tool` did not run, the plugin switched off among them, grants nothing.
