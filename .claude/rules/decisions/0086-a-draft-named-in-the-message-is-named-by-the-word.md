---
title: A draft named by number in the message a `+merge` answers is named by that word
paths: plugin/lib/claude_bestpractice/pullrequest.py
date: 2026-11-11
---

## Decision
**`+merge` names a draft pull request when the message it answers named that draft by number
or link**, whoever opened it, and it stays named once marked ready. A draft the chat only
looked at, or holds open, is still paused work no word reaches (decisions 0024, 0043).

## Why
> if another session's PR is waiting for `+merge` and he wrote `+merge` in this chat, this
> session may merge it from here

A chat named another chat's release draft as waiting for the founder, was answered `+merge`,
marked it ready and was refused as "opened by c44fb007, not this chat" (#300). The numbers a
message names lost their drafts along with the ones merely looked at.

## Rejected
- **Every draft the chat showed.** A `gh pr view` of paused work does not put it to the founder.

## Cost accepted
A session that names a draft in its message has it merged on the word that answers it. The
merge is still judged when it runs.
