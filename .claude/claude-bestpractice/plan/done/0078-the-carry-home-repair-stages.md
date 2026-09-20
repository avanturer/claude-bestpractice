---
id: 0078
title: The carry-home repair stages its move in both indexes (#208)
state: done
owner: 
branch: claude/sleepy-curie-9lzo4z
paths: plugin/lib/claude_bestpractice/migrate.py, plugin/lib/claude_bestpractice/plan.py
source: 
done_when: a committed card carried out of a worktree leaves a staged deletion there and a staged addition in the main checkout, and an uncommitted one stages nothing
blocker: 
after: 
with: 
created_at: 2026-09-19T15:49:55Z
updated_at: 2026-09-20T12:20:00Z
---

Carried over from card 0073, which claimed the #210 ledger fix. v1.62.0 fixed #210 on the
trunk with the opposite design — a transition reaches every copy, rather than every write
landing in one place — so that half of the card shipped without this branch and the card
went with it. The staging defect it also carried did not: `_carry_this_worktrees_tasks_home`
still moved a tracked file between two checkouts while telling neither index, which is #208
produced by the repair that cleans up after #208.
