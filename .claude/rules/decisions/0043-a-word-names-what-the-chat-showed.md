---
title: A word names what the chat showed, not only what it opened
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool, plugin/bin/prompt-capture, plugin/hooks/hooks.json
date: 2026-10-17
---

## Decision
**`+merge` also names each pull request the chat showed the founder by number since their
previous message**: looked at with `gh pr view`, `checks` or `diff`, read with the GitHub tool,
or a merge of it refused. Each merges once, as the chat's own do (decision 0023). One known
here is named by its record, a draft not at all, any other as `#<number>`.

**Nothing else changes.** Another chat's pull request this chat never showed is not named, a
listing names none, a number of another repository (`-R`) is not this one's, and with nothing
named the word is still the one merge.

## Why
> The session checked #936, #941 and #967 and listed them. The founder replied `+merge`.
> `gh pr merge 936` went through. The next `gh pr merge 941` was refused.

With none of its own open, the word was one merge (#276). #967, opened by another chat, was
refused after every `+merge` in the chat that had shown it (#272). The founder answered what
was put in front of them, and the gate read only who had opened what.

## Rejected
- **Every open pull request in the clone.** Decision 0023's reason stands: a sibling told to
  leave one alone would have it merged on a word said elsewhere.
- **A pull request named in the session's prose.** The plugin cannot read what it wrote, and
  text is the session's to write.
- **Saying up front that one word is one merge.** True, and it still costs a word per merge.

## Cost accepted
A pull request the chat looked at for its own reasons in the turn the founder answered is
covered by their word. It is still judged at merge time, and spent once merged.
