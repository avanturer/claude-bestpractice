---
title: The word names what the message named, and a pull request a chat opened stays its own
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/evidence-gate, plugin/bin/pre-tool
date: 2026-11-06
---

## Decision
**`+merge` also names each pull request the message it answers names by number**: a link to one
of this repository's, or `#<n>` (`owner/repo#<n>`) where a pull request on record here, open,
carries the number. Read off the turn's last message (`last_assistant_message`) by the Stop gate,
first, and counted as decision 0043 counts one the chat looked at. A bare number nothing here
records is as often an issue, and names nothing.

**A pull request a chat opened is that chat's however its record was filed.** A line that switches
branches before `gh pr create` opens the pull request of the branch it switches to, and of no
branch where it switches to one this cannot name. One GitHub has and no hook filed is the chat's
whose turn it is where that chat ran `gh pr create` in this tree within the hour before GitHub
says it was created, give or take two minutes of clocks.

This retires decision 0043's first rejection.

## Why
> **Expected.** Both merges pass on one `+merge`. That is what the gate's own refusal text
> promises.

The session opened #1139 and #1140, named both in its message, and was answered `+merge`. #1139
merged; #1140 was refused as "found on GitHub, opened by no session here", and the founder said
the word again (#295). 0043 rejected reading the message because "the plugin cannot read what it
wrote": Claude Code hands the Stop hook that message since 2.1.47, and a `gh pr view` the founder
never sees already counted.

## Rejected
- **Every `#<n>` in the message.** An issue named beside no pull request took away the one merge.
- **Reading the message out of the transcript.** It is written after the Stop hook can run.
- **Asking GitHub who opened it.** Every pull request is the founder's login.

## Cost accepted
A pull request another chat opened, named in this chat's message, is covered by the word said
here, as one looked at is. A pull request opened on the website for the chat's branch within the
hour after the chat ran `gh pr create` in that tree is taken as the chat's.
