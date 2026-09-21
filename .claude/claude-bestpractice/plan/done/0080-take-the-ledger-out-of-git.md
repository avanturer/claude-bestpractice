---
id: 0080
title: Take the ledger out of git, and count a closed card (#219, #220)
state: done
owner: 
branch: claude/jolly-euler-gpxttb
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/migrate.py, plugin/lib/claude_bestpractice/store.py, plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/evidence.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, plugin/bin/session-start, plugin/bin/claude-bp-plan, tests/test_migrate.py, tests/test_ledger_out_of_git.py
source: https://github.com/avanturer/claude-bestpractice/issues/219
done_when: the ledger is excluded per clone and untracked by the upgrade with every file still on disk; the health line stops reporting the plugin's own rule; a card this session closed answers both board demands; a red verdict names how far the tree is behind the trunk
blocker: 
after: 
with: 
created_at: 2026-09-21T16:30:00Z
updated_at: 2026-09-21T16:30:00Z
---

Decision 0018 supersedes 0001 for the ledger only. The SessionStart lines and the automatic
worktree removal from #220 are deliberately not in this release; the CHANGELOG says why.
