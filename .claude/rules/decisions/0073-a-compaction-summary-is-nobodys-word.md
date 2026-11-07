---
title: The summary a compaction leaves is nobody's word
paths: plugin/lib/claude_bestpractice/founder.py, plugin/bin/checkpoint
date: 2026-11-06
---

## Decision
**A transcript entry marked `isCompactSummary` or `isVisibleInTranscriptOnly` is never read as a
message of the founder's**, whatever it says. The compaction summary is told to keep the
founder's `+merge`, `+release` and `+migration` in backticks, as this plugin names them.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

Claude Code writes the summary as a `user` entry with no `origin` (read in its own source,
2.1.319), the shape of a turn from before the harness marked its sender, which `founder` reads
by its text. The summary is told to keep the founder's words, and keeps them: a `+merge` it
repeated was a new word, under a new id, given by nobody. arXiv 2610.29869 (October 2026)
measured this laundering in harnesses that flatten roles: 87–100% of forged approvals survived
compaction.

## Rejected
- **Telling the summary to drop the words.** The work needs them: what a `+merge` covered is
  what the next window acts on.
- **Matching the summary's opening sentence.** Its wording is Claude Code's to change; the
  flag is what it writes for the purpose.

## Cost accepted
A word the founder typed that only a summary still holds is not read back from the
transcript. `prompt-capture` heard it when they typed it.
