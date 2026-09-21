---
id: 0079
title: Off means off, and gates that blocked the exit (#215, #216, #217, #218)
state: done
owner: 
branch: claude/jolly-euler-gpxttb
paths: plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/evidence.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, plugin/bin/claude-bp, plugin/bin/claude-bp-doctor, plugin/lib/claude_bestpractice/gitpolicy.py, tests/test_offswitch.py, tests/test_gitpolicy.py
source: https://github.com/avanturer/claude-bestpractice/issues/215
done_when: the project switch and `enabled off` stand every gate down on the next call and only the founder can throw them; writing and deleting a worktree's .env is never refused by the isolation gate; `claude-bp database` uses worktree_setup where there is no psql; a convention is demanded only where the repository's log has one; trunk content is not drift; a session may remove its own worktree
blocker: 
after: 
with: 
created_at: 2026-09-21T15:10:00Z
updated_at: 2026-09-21T15:10:00Z
---

Decisions 0016 (off means off, and only the founder says it) and 0017 (a gate never refuses
the act that resolves it). Doctor check 34 proves both halves of the switch.
