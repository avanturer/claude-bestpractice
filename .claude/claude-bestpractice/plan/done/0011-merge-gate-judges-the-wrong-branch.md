---
id: 0011
title: Merge gate judges the wrong branch when the PR number is unknown
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool
source: 
done_when: 
blocker: 
after: 
with: 
created_at: 2026-08-15T09:17:33Z
updated_at: 2026-08-15T09:36:07Z
---

#135. opened() runs in PreToolUse which never sees the response, so record.number is always 0. gated_by's number check is then skipped and an unrelated PR merge is judged against the session branch. settle() and the 1.24.0 broadcast also use ctx.branch, so merging PR 501 discharges branch A's obligation and tells every other session a falsehood.
