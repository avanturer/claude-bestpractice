---
id: 0012
title: A claim does not survive a process restart
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/session-start
source: 
done_when: 
blocker: 
after: 
with: 
created_at: 2026-08-15T09:17:33Z
updated_at: 2026-08-15T09:36:07Z
---

#131. Same session id, new pid: the reaper releases the claim and resume does not take it back. The Stop demand then names files from another session and suggests add rather than re-claim.
