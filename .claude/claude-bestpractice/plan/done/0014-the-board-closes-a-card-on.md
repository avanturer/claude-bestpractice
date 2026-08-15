---
id: 0014
title: The board closes a card on assertion, and the queue never empties
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/claude-bp-plan
source: 
done_when: done refuses a card with no finish condition, and a 21-day-old queued card moves to paused
blocker: 
after: 
with: 
created_at: 2026-08-15T10:32:14Z
updated_at: 2026-08-15T10:49:46Z
---

done_when is carried everywhere and required nowhere, so done is a rename - the assertion decision 0002 refuses everywhere else, as plan.py's own comment says. And sweep_idle only moves doing to next, so next is a one-way sink.
