---
title: A session goes back into its tree without the founder being asked, wherever the tree was made
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/pre-tool, plugin/bin/session-start
date: 2026-11-12
---

## Decision
**`EnterWorktree` into a tree of this repository outside `.claude/worktrees/` has the gate move
the tree there first**, with `git worktree move` and no `--force`, and refuse once naming the new
path; the call after it asks nobody. Not under `bypassPermissions`, which is asked nothing. Never
from under another chat's live session or a process working in the tree; where processes cannot
be read, only a tree this plugin made. This chat's records in the tree and the cards they hold go
with it, under the identity the new path composes.

**A session's tree is the one this plugin made for it, else the one on the branch of a card it
holds, else the one its chat last worked in**, read off its records and the reap log. The board
names it, and a write refused in the main checkout is sent there rather than to a new tree.

**No line says an allow rule ends the prompt.** None does.

## Why
> Сессия после сбоя WSL всё ещё привязана к главному checkout ... и меня спрашивает можно ли
> ему войти в ворктри, нужно что бы он без разрешения входил всегда

Claude Code's worktree docs: a path outside `.claude/worktrees/` "prompts for your approval
before entering", and "an `EnterWorktree` permission rule or choosing 'don't ask again' doesn't
suppress this prompt; only `bypassPermissions` mode skips it". On resume Claude Code declines to
re-enter a tree that fails its checks. The board named no tree for one made by hand, and its
advice was the allow rule.

## Rejected
- **`updatedInput` on the hook.** Whether the prompt is judged on the rewritten path is not
  documented; a refusal and a second call are.
- **`cd` instead.** Outside the project directory it does not persist between calls.
- **Moving every tree at session start.** Repair 0005 moves this plugin's; another tool's tree
  is moved when its own chat asks to go into it.

## Cost accepted
One more call on the way back into such a tree. A tree another chat or a process works in still
asks the founder, and a move under an editor holding the folder outside `/proc` is not seen.
