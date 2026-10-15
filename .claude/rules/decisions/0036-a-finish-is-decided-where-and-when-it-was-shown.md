---
title: A finish is decided on the commit and in the chat it was shown in
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool
date: 2026-10-15
---

## Decision
**A refusal or hand-off that lists an UNVERIFIED finish records the commit it was read at and
the session it reached.** The founder's next `+merge` decides a finish filed on that commit,
including one first filed there after the showing, and it decides a finish shown in that chat
whether or not the word names the pull request.

**A merge refused over a finish not yet decided reads the transcript first**, once, and judges
again: a word sent while the session worked reaches no hook, and a word already on record made
the gate skip the transcript.

Decision 0010 holds: a word said before the showing decides nothing, a commit at which no finish
was listed is not decided, and a later commit is put to them again.

## Why
> `+merge` снимает «UNVERIFIED finish» для этой головы, как сказано в сообщении гейта.

Refused, shown, `+merge`, the same refusal with the same items (#268), as after #241, #243, #246.

## Rejected
- **Dropping UNVERIFIED from the merge check.** It is the one blocker that is theirs to decide.
- **Reading the transcript before every merge.** Up to 64 MB, on a path nothing else waits on.

## Cost accepted
A word in a chat decides a finish shown there on a pull request another chat opened. The merge
still needs the word to cover it, which decision 0023 decides.
