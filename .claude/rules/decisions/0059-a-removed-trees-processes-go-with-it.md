---
title: A removed tree's processes go with it
paths: plugin/lib/claude_bestpractice/worktree.py
date: 2026-10-30
---

## Decision
**Once git has removed a tree, the processes still running in it are stopped**, before its
database is given back: asked to stop, and killed if they have not within five seconds. A
server holding the database kept it from being dropped. The founder's own processes only, only those
whose directory was the tree or under it, and never the gate itself, the processes above it or
a Claude Code session. The line that reports the removal names them.

Read through `/proc`; where processes cannot be read that way, macOS among them, nothing is
stopped. A removal git refuses stops nothing.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

A dev server started in a tree outlived `git worktree remove`. Metro, expo and uvicorn went on
holding the tree's port and its database, serving code no longer on disk, on a machine that
already ran six to twelve sessions; the next tree's server found the port taken.

## Rejected
- **Stopping them before the removal.** git may still refuse it, and the tree stays with its
  servers gone.
- **Naming them on the board instead.** The tree is gone; nothing a session does there is the
  work of anyone, and a pid on a board is a chore handed to the founder.
- **Every process with the tree's path in its command line.** An editor or a shell elsewhere
  that names a file in it is not running in it.

## Cost accepted
A process deliberately left serving out of a finished tree stops with it. A process whose
directory was elsewhere but which holds the tree's port is not found.
