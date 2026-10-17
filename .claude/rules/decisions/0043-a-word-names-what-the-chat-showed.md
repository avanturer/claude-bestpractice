---
title: A word names what the chat showed, not only what it opened
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/founder.py, plugin/bin/pre-tool, plugin/bin/prompt-capture, plugin/hooks/hooks.json
date: 2026-10-17
---

## Decision
**`+merge` also names each pull request the chat showed the founder by number since their
previous message**, one they sent into a running turn included: looked at with `gh pr view`,
`checks` or `diff`, read with the GitHub tool, or a merge of it refused to them. Each merges
once, as the chat's own do (decision 0023). One known here is named by its record, any other
as `#<number>`, kept by later words until it merges or is recorded closed.

**Only this repository's, and only what can still merge.** A call naming another repository
(`-R`, a URL, `GH_REPO`, the tool's owner and repo) or run in another clone shows none, and
neither does a listing, a draft, or a pull request recorded as merged or closed. With nothing
named the word is still the one merge. Reading one is let through uncounted.

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
covered by their word. It is still judged at merge time, and spent once merged. One nothing here
records, already merged on GitHub, takes the place of the one merge: the next is refused naming
it, and the word is said again.
