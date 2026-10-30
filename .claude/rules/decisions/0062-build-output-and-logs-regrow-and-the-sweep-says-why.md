---
title: Build output and logs regrow, and the sweep says why it keeps a tree
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/claude-bp
date: 2026-10-31
---

## Decision
**Build output and logs are among what regrows** (decision 0039): `build`, `dist`, `target`,
`.cxx`, `Pods`, `DerivedData`, `coverage`, `htmlcov`, `logs`, `log` and `*.log`. A
directory git reports whole, because nothing in it is tracked, regrows when everything in
it does.

**`claude-bp sweep` names each tree it leaves, and why**: the ignored files no other tree
has, a submodule, a directory already gone, a detached HEAD, or git's own refusal.

## Why
> `claude-bp sweep` printed `nothing to remove` and removed nothing, with 6 trees named by the
> board as finished. In five of the six these were only `backend/logs/`, `logs/` and Android
> `build/` directories (#285)

Decision 0039 already counted build output as regrowing, and the list in the code never did.
And the one line the sweep printed was true of none of the six trees.

## Rejected
- **A switch naming what is disposable.** The founder would maintain a list the plugin
  should know; a project with real data under `build/` names it otherwise, or keeps it out
  of an ignored path.
- **Removing with `--force` past the ignored files.** That is how 75 photos went (0039).

## Cost accepted
A log kept for evidence in a finished tree's `logs/` goes with the tree. Results written into
an ignored `build/` or `dist/` count as build output.
