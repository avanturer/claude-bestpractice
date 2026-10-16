---
title: A tree holding what nothing else has stays, and a tree just touched is nobody's only later
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/session-start
date: 2026-10-16
---

## Decision
**A tree is removed only when its ignored files are ones the clone gets back by itself.** What
regrows (`node_modules`, caches, virtualenvs, build output), what the main checkout holds
byte for byte, the `.env` this plugin wrote, and the plugin's own state do not keep it. Any
other ignored file does, and the board names the tree with the command that lists those files.

**A tree touched in the last two hours is not called nobody's.** Its directory, its `HEAD` and
its reflog say when it was last worked in; the index does not, because session start touches it.

This revises the cost decision 0032 accepted: the ignored files of a finished tree no longer go
with it unless they are of those kinds.

## Why
> Перед любым ручным сносом дерева — `git status --ignored`.

`git worktree remove` without `--force` refuses a modified or untracked file and deletes every
ignored one without a word. A founder's export of store-listing photos, 75 files in an ignored
directory of a finished tree, went with the tree, and the board had called a sibling's tree made
a moment earlier one that "loses nothing".

## Rejected
- **`git status --ignored` empty as the condition.** Every tree that ran a build would stay.
- **Moving the ignored files somewhere before removing.** A copy nobody asked for, of data the
  plugin cannot tell from a cache, in a place nobody looks.

## Cost accepted
A finished tree holding a scratch file nothing else has stays until somebody removes it, named on
the board. A tree left two hours idle with nothing in it is named a little later than before.
