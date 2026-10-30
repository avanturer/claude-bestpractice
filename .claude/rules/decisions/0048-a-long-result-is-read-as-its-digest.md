---
title: A long successful result is read as its digest, and the whole is kept
paths: plugin/hooks/bestpractice.ts, plugin/lib/claude_bestpractice/digest.py, plugin/lib/claude_bestpractice/outputs.py, plugin/bin/claude-bp-mod
date: 2026-10-30
---

## Decision
**A test suite, build or install that succeeded and printed 6,000 characters or more reaches
the model as a digest:** the runner's summary, every line that looks like an error or a
warning, the head and the tail, and where the whole output is. So does any other output Claude
Code would have cut to its first 2 KB. The whole is kept under `.claude/claude-bestpractice/out/`,
out of git, the newest forty for a week; one Claude Code persisted itself is named, not copied.

**Never a read and never a failure.** Output a session asked to see is left whole, and a command
that failed reaches the model as Claude Code keeps it. `compress_output off` hands everything over.

## Why
A passing `pytest -v` of 10,153 tests in a real project printed 2.5 MB: one summary line, ten
warnings, and twenty thousand lines of `PASSED`. Cut to 2,600 characters in 0.3 s, it keeps all
three things a reader acts on. A month of one user's persisted outputs was opened 36 times in 172.

## Rejected
- **A digest of failures.** The middle of a failure is gone before a hook sees it, and reordering
  what is left reads worse than Claude Code's own head and tail.
- **A tool of our own that runs commands** (context-mode's way). A second Bash the session must
  remember to use; WebSearch's routing shows the transparent way works.
- **A full-text index of kept outputs.** Grep over a kept file finds the same lines, standard library only.

## Cost accepted
A session that wanted a passing line the digest dropped reads it from the file the header names.
The founder sees the digest in the transcript too.
