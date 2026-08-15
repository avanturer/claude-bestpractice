---
id: 0019
title: A green run is stamped with the tree it was green on
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: plugin/lib/claude_bestpractice/evidence.py
source: 
done_when: 
blocker: 
after: 
with: 
created_at: 2026-08-15T11:25:02Z
updated_at: 2026-08-15T11:58:44Z
---

record_green writes command, time and branch, so the push gate cannot tell green-now from green-three-edits-ago and re-runs the suite it just ran.
