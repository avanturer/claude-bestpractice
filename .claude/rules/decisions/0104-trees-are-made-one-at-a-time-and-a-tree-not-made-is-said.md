---
title: A clone's trees are made one at a time, and worktree-create answers with a path or a reason
paths: plugin/bin/worktree-create, plugin/lib/claude_bestpractice/worktree.py
date: 2026-11-20
---

## Decision
**A clone's trees are made one at a time**: the path chosen, `.git/info/exclude` written and
`git worktree add` run under one lock in the git common dir (`worktree.ADD_LOCK`), by the
`WorktreeCreate` hook and the tool gate's `provision` alike. The hook waits up to 45 of its 60
seconds for the trees asked for before it; the gate 5 of its 15, then names the command as before.

**`worktree-create` answers with the tree's path or exits 2 saying why not**: git's own words,
the wait that ran out, no repository, or a crash. It fails closed, a tree it made and then could
not finish setting up included.

## Why
> An empty answer from WorktreeCreate means Claude Code gets no tree for that agent, so this
> matters outside the test too.

Four hooks at once beside `make test-fast`: in 3 of 60 bursts one printed nothing, each on
`fatal: failed to read .git/worktrees/agent-1/commondir: Success`. Every `git worktree add` reads
each listed tree's `commondir` as it starts and dies on an empty one, and the add making a tree
creates that file before writing it; git 2.56's source does both still. The hook exited 0 with no
output, and Claude Code's hooks reference says "If the hook fails or produces no path, worktree
creation fails with an error". No default tree is made instead, so failing open bought nothing.
anthropics/claude-code#104026 reports the race for Claude Code's own parallel agents.

## Rejected
- **Retrying git on that error.** A guess at how long a sibling takes to write one line.
- **Holding the lock through `furnish`.** A project's `worktree_setup` would run one agent at a time.
- **Printing the path of a tree whose setup crashed.** Untrusted or without its database, it
  looks like a model failure; the crash is a defect to see.
- **A clone per agent.** Every gate here reads one clone's trees.

## Cost accepted
Trees asked for together are checked out in turn, about 0.1 s each for 515 tracked files: agents
of a large checkout past the 45 seconds are refused, saying so. A `git worktree list` outside the
lock, another gate's or `furnish`'s, can still die on that file and read no trees. A branch an add
made before git died stays until the merged-branch sweep takes it.
