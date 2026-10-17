---
title: A session that removes the tree it stands in is told the way out
paths: plugin/bin/pre-tool, plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/worktree.py
date: 2026-10-16
---

## Decision
**`git worktree remove` of the tree a session stands in is performed from the main checkout,
whoever made the tree.** Decision 0022 did it for the trees this plugin makes; a tree made by
hand is taken over the same way where no other chat works in it.

**The way out of a removed tree is `ExitWorktree` with action `keep`.** The refusal that did the
removal names it, and so does the Stop gate when the tree it removes is the one the shell stands
in: there as an instruction, while the turn is still open. `cd` stays the way out for a session
that came in with `cd`, for which `ExitWorktree` does nothing.

## Why
> `git -C <main checkout> worktree prune && git -C <main checkout> branch -D x` → refused.

Measured on Claude Code 2.1.301: once the tree a session entered with `EnterWorktree` is gone,
every git command aimed outside it is refused, `git -C <main> branch --list` and `cd <main> &&
git …` included, and `ExitWorktree` with `keep` still works and ends it. A note left for the next
tool call never arrives, since those hooks run outside every repository (#271).

## Rejected
- **Lifting the refusal.** It is the harness's isolation, not this plugin's to undo.
- **`ExitWorktree` with `remove`.** It refuses a branch with commits the original branch lacks,
  which a squash leaves, unless told to discard them.
- **Telling a session after every removal.** A shell standing elsewhere is not left anywhere.

## Cost accepted
A Stop that removes the tree its shell stands in costs the session one more step.
