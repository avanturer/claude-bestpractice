---
description: Review the current diff with a fresh-context agent that cannot edit
argument-hint: "[baseline ref, defaults to the session baseline]"
allowed-tools: Bash(git:*), Task
---

Baseline: $1

Current diff:
!`git --no-pager diff --stat ${1:-HEAD} | tail -30`

Spawn the `independent-reviewer` agent against this diff. Give it the baseline ref, the
list of changed files, and the task statement from `.claude/founder-os/plan/doing/` if
one exists.

Do not review it yourself first and do not summarise your own opinion into the prompt —
the whole value is that the reviewer has none of your context. Pass the facts and
nothing else.

When it returns, report its findings unchanged. If it found nothing, say so plainly
rather than looking harder for something to report.
