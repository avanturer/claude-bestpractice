---
title: A removal carries its own cleanup, and happens where the session survives it
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/pre-tool, plugin/lib/claude_bestpractice/migrate.py
date: 2026-09-21
---

## Decision
**Removing a worktree removes what the worktree was given.** The branch it stood on, the
database this plugin derived and wrote into its `.env` when nothing else points at it, and
the local branches beside it whose work is already in the trunk. One act, on the same
proofs decision 0019 accepts: `git worktree remove` without `--force`, `git branch -d`, and
`-D` only where every file a branch delivers is byte-identical to the trunk. `drop database`
without FORCE, so a server holding a connection keeps it.

**A session never removes the tree it is standing in.** `git worktree remove <own tree>`
through Bash is intercepted and performed by the gate, from the main checkout, and the call
itself is denied as already done. State left by removals that happened before this is
finished by a migration on the next session start.

## Why
> Выполнить «из своего worktree» нечего: его только что и удалили.

git removes the directory the shell is standing in and returns 1 on the `getcwd` that
follows, after which Claude Code refuses every git command in the session as *isolated in
the worktree `<gone>`*. Non-git commands still run, so it is not #174 — but the branch, the
database and the sibling branches are all git and all unreachable, and the session ends
handing the founder a list of commands to run for it. On the reporting machine that was
three merged branches and a sixteen-megabyte database per finished task, accumulating.

## Rejected
- **Refusing the command.** It is the act this plugin asks for; only the place is wrong
  (decision 0017). A refusal here also has no runnable remedy — that is the defect.
- **Naming the leftovers on the board.** What 0019 does for trees it may not touch. These
  it may: it created them, and a name the founder has to act on is the thing #220 measured
  the cost of.
- **Dropping any database the tree pointed at.** Only the name this plugin derived, and only
  when no other working tree names it. Everything else is somebody's data.
- **`--force` on this plugin's own initiative.** Passed through only where the session typed
  it; git's refusal is still what protects uncommitted work.

## Cost accepted
The branch sweep is repository-wide rather than tree-wide: branches belong to no tree, so
"this session's" is not a question git can answer. A merged branch kept deliberately as a
label is deleted — recoverable from the trunk, which is where its content already is.
