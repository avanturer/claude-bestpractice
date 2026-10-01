---
title: A command writes the index of the tree it runs in, and no other
paths: plugin/lib/claude_bestpractice/plan.py, plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-10-01
---

## Decision
**A card's move is staged only in the tree the command runs in.** Every transition still moves
every copy of the card on disk, in every tree; the index of any other tree is left to the
session working there. So is every repair's, save 0038, which writes them once to take back
the deletions earlier versions staged there.

**A tracked copy of a card the main checkout holds is not work.** A tree cut from a trunk that
still tracks the ledger shows a copy another tree moved as a tracked file deleted. The
finished-tree check does not count it, and the removal marks those copies `--assume-unchanged`
in that tree's own index just before `git worktree remove`, which still runs without `--force`.

## Why
> Переход задачи не меняет индекс чужого worktree. Если копию надо вывести из git, это делается
> в своём дереве или откладывается до сессии, которая в этом дереве работает.

Closing seventy-three cards from the main checkout left fourteen staged deletions in every
worktree, a live sibling's with an open pull request among them, and its next commit carried
them into a pull request about something else (#254). An index is what the next `git commit`
there takes, and only the session standing in that tree knows what that commit is for.

## Rejected
- **Moving only the copy in this tree.** The reader ranks copies by state, so a stale copy
  elsewhere would put the card back on the board (#123).
- **Staging everywhere and unstaging before each commit.** A commit made with plain `git` never
  asks the plugin.
- **`git worktree remove --force`.** It takes the copies and anything else in the tree with
  them; the mask covers the copies and nothing more.

## Cost accepted
Until a session works in it, another tree shows each moved copy as an unstaged deletion, which
no commit takes without being told to.
