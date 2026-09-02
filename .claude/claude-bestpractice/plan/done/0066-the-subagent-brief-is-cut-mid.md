---
id: 0066
title: The subagent brief is cut mid-anchor, so the entity it names cannot be resolved
state: done
owner: 
branch: claude/skill-state-token-efficiency-ydpwsu
paths: plugin/lib/claude_bestpractice/knowledge.py, tests/test_knowledge.py
source: 
done_when: no brief ever ends inside an entry, a test drives a brief past the budget and asserts the last line is a whole anchor, a
blocker: 
after: 
with: 
created_at: 2026-09-02T17:16:12Z
updated_at: 2026-09-02T17:27:01Z
---

knowledge.subagent_brief hard-truncates at BRIEF_CHAR_BUDGET and the cut lands inside an entry: the brief measured on this repository ends '... @ plugin/lib/claude_bestpractice/evidence', an anchor sliced in half, with 913 chars discarded. A half anchor is worse than no anchor because it names a path that does not exist. Truncate on entry boundaries instead, so the brief ends on the last entity that fits whole.
