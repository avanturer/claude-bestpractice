---
title: A session's work is what no remote branch but its own already carries
paths: plugin/lib/claude_bestpractice/gitctx.py
date: 2026-10-08
---

## Decision
**What a remote branch other than this branch's own already carries is somebody else's work.**
The diff every gate reads is measured from the commits the session's own commits stand on, and
its own are the commits HEAD gained since the session started that no such branch carries. The
trunk was the only one asked; a pull request's branch is the other place work arrives from.

**Its own is the remote branch of the same name, never its configured upstream:** a branch cut
from `origin/theirs` tracks it. The trunk is never a branch's own, so work pushed there has
landed. With no branch checked out only the trunk is asked, and local branches never are.

**The floor still rises only past where the session started.** Joined to two branches neither of
which carries the other, it steps over the one with more commits.

## Why
> code brought in by merge/fast-forward is not attributed to the current turn.

A session fast-forwarded onto another pull request's branch to build on it, and a stub in that
branch refused its finish four times as "introduced in this turn"; the UNVERIFIED mark it left
held the merge (#258). v2.0.1 left this half open over the widening below.

## Rejected
- **Every remote branch.** The session's own among them: its work leaves the diff the moment it
  is pushed, and every gate that reads the diff stops seeing it.
- **The configured upstream as its own.** `git checkout -b mine origin/theirs` makes it theirs,
  and a branch cut from `origin/main` would make the trunk its own.
- **Local branches.** `git branch backup` before a rebase would make the whole history somebody
  else's.
- **The reflog.** It expires, can be switched off, and its messages are not an interface.
- **Only branches with a pull request on record.** One opened from another clone is not.

## Cost accepted
Work a session pushed is no longer its own once another session pushes a branch built on it, or
once it pushes that work under another name. A pull request cut from a trunk older than the
session's start is still counted as the session's when merged in: stepping over it would hand
the session the trunk commits between that fork and its start.
