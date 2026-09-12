---
id: 0071
title: A single test command per repository, and a failure rediscovered four times (#206)
state: done
owner: 
branch: claude/jolly-euler-gpxttb
paths: plugin/lib/claude_bestpractice/suites.py, plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/evidence-gate, tests/test_suites.py, tests/test_gates.py
source: https://github.com/avanturer/claude-bestpractice/issues/206
done_when: a mobile-only diff runs only the suites it touches; a recorded failure is re-asserted rather than re-run; the gate does not ask four times what it forbade
blocker: 
after: 
with: 
created_at: 2026-09-12T11:00:00Z
updated_at: 2026-09-12T11:00:00Z
---

Path-scoped suites (`test_commands` plus detection), a red record stamped with the suite and
the tree it judged, the block streak collapsed where the pull-request demand had already told
the session to stand down, and `tree_hash` no longer reading every live session as dirty.
