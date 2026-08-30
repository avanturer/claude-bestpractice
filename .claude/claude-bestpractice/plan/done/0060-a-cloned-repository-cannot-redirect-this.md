---
id: 0060
title: A cloned repository cannot redirect this plugin's writes
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/store.py, plugin/lib/claude_bestpractice/policy.py, plugin/lib/claude_bestpractice/limits.py, plugin/lib/claude_bestpractice/worktree.py, tests/test_store.py
source: 
done_when: a symlink planted inside .claude/claude-bestpractice cannot carry a write out of the tree, and the founder's own dotfile
blocker: 
after: 
with: 
created_at: 2026-08-30T09:51:51Z
updated_at: 2026-08-30T09:57:34Z
---

v1.55.0 taught atomic_write to follow a symlink so nix/home-manager and stow would stop losing ~/.claude/settings.json. That was right for the founder's home and wrong everywhere else: most writes land in .claude/claude-bestpractice/ INSIDE the repository, which arrived by git clone and can ship a symlink pointing anywhere. Demonstrated: a planted failing-suite.json link, and record_red wrote through it to a file outside the tree — from a hook, where no permission check stands. append_jsonl had the same escape and older, since os.open follows links without O_NOFOLLOW and os.replace at least replaced them. Claude Code fixed the same class in its own file tools in 2.1.251.
