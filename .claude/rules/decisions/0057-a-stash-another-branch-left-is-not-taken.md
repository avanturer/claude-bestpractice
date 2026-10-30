---
title: A stash another branch left is not taken off the list
paths: plugin/lib/claude_bestpractice/gitpolicy.py, plugin/bin/pre-tool
date: 2026-10-30
---

## Decision
**`git stash pop`, `drop`, `clear` and `branch` are refused when the entry they take was
stashed on another branch**, in the session's own tree as anywhere else. The entry is read off
the clone's stash list, by the branch its subject names; `stash@{0}` when the command names
none. The refusal names `git stash apply <entry>`, which brings the work in and leaves the
entry on the list.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

Every worktree of a clone shares one `refs/stash`, so its top entry is whichever tree stashed
last. Checked live: `git stash pop` in one session's tree took the unfinished work another
session's tree had stashed a minute before, and that work was then nowhere but in the wrong
tree. Nothing names a file, so no lease and no card saw it.

## Rejected
- **Refusing every stash in a shared clone.** A session's own stash is its own to pop.
- **Refusing `apply` too.** It copies, and the tree that stashed it keeps its entry.
- **Asking which session is live on that branch.** An entry outlives its session, and the
  work in it is still somebody's until they drop it.

## Cost accepted
An entry stashed on a branch since deleted is applied and then dropped by hand. A stash taken
in a detached HEAD names no branch and is not judged.
