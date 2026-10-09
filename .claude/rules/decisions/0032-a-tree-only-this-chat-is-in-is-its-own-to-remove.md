---
title: A tree only this chat is in is its own to remove, where no pid can tell
paths: plugin/bin/pre-tool, plugin/lib/claude_bestpractice/gitpolicy.py, plugin/lib/claude_bestpractice/sessions.py
date: 2026-10-09
---

## Decision
**`git worktree remove` without `--force` goes through on a tree where every live session
carries this session's harness id**, unless both pids were resolved and name two processes.
git refuses that removal while anything in the tree is modified or untracked, so it takes
nothing uncommitted. It is the one act let through on that evidence: `--force`, `reset`,
`clean`, `stash`, a checkout and a write there are refused as before.

**A refusal never names this chat as somebody else.** Its own record is marked `[this chat]`,
another process of it `[another process of this chat]`, and a tree only this chat is in is
called that, with the removal that is allowed named as the way out.

**A removal's path is read where git reads it**: after every `-C`, not from the shell.

## Why
> The session named as the occupant is the caller itself (44d9e3a4).

Without /proc no pid names a process, so #89's rule never applied there, and a tree made by
hand is in no registry for the tree link to read. A session that finished in one tree and went
on in the next was refused the removal of the first as another session's: three trees, two
sessions, two days, and nine finished trees removed by hand (#263).

## Rejected
- **One harness id as one session wherever pids cannot tell.** A `claude -p` shares its
  parent's id, and a write, a reset or a forced removal takes its uncommitted work.
- **Asking `claude agents --json` for the process.** Up to five seconds on a tool call;
  decision 0028 keeps that question to session start.
- **Dropping the record once the chat works elsewhere.** A subagent or a `claude -p` of the
  chat may still be in that tree.

## Cost accepted
A `claude -p` this chat started in that tree, with nothing uncommitted, loses its directory
when the chat removes the tree, and the ignored files there go too, `.env` among them: that is
what removing a finished tree is for. Two resolved pids still keep it refused.
