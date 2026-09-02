---
id: 0064
title: Compaction hands back prose and a path list instead of the work
state: done
owner: 
branch: claude/skill-state-token-efficiency-ydpwsu
paths: plugin/bin/checkpoint, plugin/bin/session-start, tests/test_gates.py
source: 
done_when: the claimed card's body and paths survive a restore at 45 changed files, the opening request still survives, and a test
blocker: 
after: 
with: 
created_at: 2026-09-02T17:15:45Z
updated_at: 2026-09-02T17:23:19Z
---

checkpoint writes Opening request, Recent turns, up to 60 changed-file lines, then _carried() last; session-start:198 restores the first 2400 chars head-first, so _carried() is the first thing cut. Measured: with turns at the 300-char cap the attempt log goes at 21 changed files and the ledger heading at 22; with no transcript it survives to 41. The repo's own test test_the_goal_the_plan_and_the_dead_ends_all_come_back passes at 1 file and fails at 25. Only task.body and task.paths are unrecoverable — board.render already re-injects titles, attempts and open items in the same payload — so the fix is to keep those and drop what the board duplicates, NOT to reorder: _carried() alone can exceed 2400 on eight attempts and would then evict the opening request, which test_gates.py:525 asserts survives.
