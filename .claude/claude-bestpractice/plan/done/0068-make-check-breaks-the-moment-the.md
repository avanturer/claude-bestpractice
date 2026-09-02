---
id: 0068
title: make check breaks the moment the plugin provisions a worktree
state: done
owner: 
branch: claude/skill-state-token-efficiency-ydpwsu
paths: tools/check_slop.py, tools/check_polyglot.py, tests/test_release.py
source: 
done_when: make check passes with a provisioned worktree present, the exclusion comes from git rather than from a hardcoded directo
blocker: 
after: 
with: 
created_at: 2026-09-02T19:18:07Z
updated_at: 2026-09-02T19:32:50Z
---

check_slop.py walks ROOT.rglob('*.py') with _SKIP_DIRS = {.git, node_modules, __pycache__, .venv, venv, dist, build, target} — no entry for a nested worktree. The plugin provisions worktrees under .claude/worktrees/ by its own design, so a full second copy of the repository appears inside ROOT and the checker reports it as the founder's code: measured 22044 duplicate_blocks against a budget of 0, plus complex_functions 38>19 and long_functions 10>5, and make check exits 1. That is the definition of done failing for anyone using the plugin's default worktree flow. No tool under tools/ mentions worktrees at all, so the scope question was never asked. git worktree list is the source of truth for which subtrees are not ours to check.
