---
title: A tree list git cannot give is no list, and each reader says what not knowing means
paths: plugin/lib/claude_bestpractice/gitctx.py, plugin/lib/claude_bestpractice/gitpolicy.py, plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-11-20
---

## Decision
**A `git worktree list` that fails is never "no trees"**: one that succeeds names the main checkout.
`gitctx.worktree_paths`, the one reading of it, raises `GitError`, and each reader decides.
A call aimed outside the session's tree is **refused** with git's words, and so is a main-checkout
write whose session's own tree cannot be found: no second tree is cut or named. A removed tree's
database is **kept**. A decision or an attempt is **not numbered**, and the Stop gate files the
UNVERIFIED finish without its attempt. **No repair runs**, and a step reading every tree raises.
The ledger reads the main checkout and this tree. The board, an approval, a move home: **unsaid**.

**`worktree.main_checkout` is worked out as git does**: the common dir's real path, less `/.git`.

## Why
> Find which gates and library functions in `plugin/` read the clone's worktree list without
> holding `worktree.ADD_LOCK`, and decide for each one what an empty or failed listing does to it.

Measured on git 2.43 over a frozen half-written `commondir` (decision 0104): `worktree list`,
`branch` in its own format, `branch -d`/`-D`, `worktree remove`, `checkout` and
`%(worktreepath)` die with 128; `branch --format=%(refname:short)`, `status`, `rev-parse`,
`merge --ff-only` and `worktree prune` do not. Read as empty, a write and a `reset --hard` into a
live sibling's tree went through; a card from a linked tree was written there, numbered `0001`
beside a `0001`; a removal could drop a database another tree named; the Stop gate judged a
session in the main checkout, where nothing had changed; repairs that saw one tree were done.

## Rejected
- **`ADD_LOCK` in every reader.** The tool gate has 15 seconds, and the hook may hold it 45.
- **Retrying the listing.** Decision 0104's reason stands.
- **Reading `.git/worktrees/` by hand.** A second reading of git's register, kept in step with it.
- **The ledger's numbers refused too.** Its home is known without git; a card refused is lost.

## Cost accepted
While an add is caught halfway, a call aimed outside the session's tree is refused, `/tmp`
included, and decisions and attempts wait. An entry a killed add left half written keeps them
refused until it is repaired; the refusal names `git worktree list`, which says so.
