---
id: 0063
title: A forked session starts with no board and no record
state: done
owner: 
branch: claude/skill-state-token-efficiency-ydpwsu
paths: plugin/hooks/hooks.json, tests/test_gates.py
source: 
done_when: hooks.json matches fork, a test drives session-start with source=fork and asserts the board comes back, and the $comment
blocker: 
after: 
with: 
created_at: 2026-09-02T17:15:45Z
updated_at: 2026-09-02T17:19:29Z
---

SessionStart's source enum gained 'fork' in Claude Code 2.1.258 and the matcher in plugin/hooks/hooks.json is still startup|resume|clear|compact. Matchers filter on source, so on a forked session the whole session-start gate does not run: no board, no session registration, no reap, no pre-push arming, no checkpoint restore. Verified against the hooks documentation, which lists fork among the SessionStart sources. The file's own $comment is also stale: it says nine entries across eight events, the file holds eleven across ten, cap twelve.
