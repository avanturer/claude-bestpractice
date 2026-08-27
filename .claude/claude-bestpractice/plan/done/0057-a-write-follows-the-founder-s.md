---
id: 0057
title: A write follows the founder's symlink instead of replacing it
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/store.py, tests/test_store.py
source: 
done_when: atomic_write onto a symlinked path leaves the link intact and the content at its target
blocker: 
after: 
with: 
created_at: 2026-08-27T10:27:46Z
updated_at: 2026-08-27T10:39:03Z
---

store.atomic_write does os.replace onto the path, which replaces a SYMLINK with a regular file. ~/.claude/settings.json is written by policy.apply from a hook on every session, and dotfile managers (nix/home-manager, stow, chezmoi) keep it as a link — so the link does not survive the first session, and the founder's next switch either conflicts or silently reverts the plugin's writes. Measured on a simulated dotfiles layout: is_symlink True before, False after, target still holding the old content. Same class of bug Claude Code 2.1.247 fixed in its own sandbox cleanup.
