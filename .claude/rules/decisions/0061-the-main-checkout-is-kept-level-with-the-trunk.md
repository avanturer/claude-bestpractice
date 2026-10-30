---
title: A session starting in the main checkout brings it level with the trunk
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/session-start
date: 2026-10-30
---

## Decision
**A session that starts in the main checkout fast-forwards it to the trunk it lags**, before its
baseline is taken: `git merge --ff-only origin/<branch>`, only on a trunk branch, and only where
no other live session is in that tree. Nothing is fetched; the trunk is as the clone last saw
it. The board says what was done, or names git's refusal and the command, or that the tree has
commits of its own and is left where it is.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

Sessions work in their own trees and merge on GitHub, so nothing moved the main checkout. A
founder's stood 107 commits behind `origin/main`, and every session started there read code the
trunk had replaced, was told to fix a red suite on it, and measured its work from it. Decision
0018 had already made that tree the one kept level; nothing kept it so.

## Rejected
- **Fetching first.** A network call on every session start, from a hook with thirty seconds.
- **Merging or rebasing when a fast-forward is refused.** It writes a commit in the tree every
  session shares, about work nobody in it asked for.
- **Moving it from a session in another tree.** A session reading in the main checkout would
  have its files change under it; the tree's own session is the one that is starting.

## Cost accepted
A session starting in the main checkout begins with a tree that changed since the founder last
looked at it there. A file the trunk would overwrite keeps the tree where it is until someone
commits or moves it, and the board says so at every start in it.
