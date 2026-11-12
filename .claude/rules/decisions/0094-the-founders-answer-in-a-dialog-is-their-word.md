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

This revises decision 0010: the word is read from the founder's own message, or from their own
answer to a dialog.

## Why
> Гейт +merge не видит ответ в диалоге AskUserQuestion: основатель выбрал вариант «+merge», но
> запись считается только из обычного сообщения.

No hook heard the pick: it starts no turn, and the transcript files it as a tool result, which
the reader of their words skips (#326). The merge refusal told the session to ask, and asked in a
dialog, the founder answered into nothing.

## Rejected
- **A thirteenth hook entry.** The budget is twelve; `pr-opened` already runs on PostToolUse.
- **Any label holding the word.** The session writes the labels: «Show the diff, then +merge»
  picked for the diff would merge.
- **Reading the dialog out of the transcript.** Its result's text quotes questions and labels
  the session wrote, unmarked.

## Cost accepted
Whatever answers a dialog for the founder answers as them, a hook or a `claude -p` permission host
included, whoever set it up: Claude Code's result does not say who picked. A label that explains
itself beside the word is not read; the explanation goes in the option's description.
