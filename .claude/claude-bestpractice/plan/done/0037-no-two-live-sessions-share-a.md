---
id: 0037
title: No two live sessions share a database: derived per worktree, written at birth, refused on collision
state: done
owner: 
branch: claude/recheck-after-a-merge
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/config.py, plugin/bin/worktree-create, plugin/bin/pre-tool
source: 
done_when: a second session on the same database is refused at its first write, each worktree is born with its own DATABASE_URL, an
blocker: 
after: 
with: 
created_at: 2026-08-22T11:08:52Z
updated_at: 2026-08-22T11:16:08Z
---

(no detail)
