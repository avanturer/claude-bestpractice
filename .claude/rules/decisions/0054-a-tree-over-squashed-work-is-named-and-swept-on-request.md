---
title: A tree over squash-merged work is named, and swept when asked
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/claude-bp, plugin/bin/session-start
date: 2026-10-30
---

## Decision
**A tree nobody is in counts as finished when its branch's work is in the trunk by ancestry, by
patch or by content**, the test decision 0041 puts to a branch owed a pull request. The board
names it among the trees that lose nothing when removed.

**`claude-bp sweep` removes the trees that line names**, the way the plugin removes its own: no
`--force`, and the branch only on proof its work is in the trunk. A session start still removes
none of them; decision 0019 stands for trees another tool made.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

A founder's clone held 24 trees, 22 GB, over branches whose pull requests had been squash-merged.
Ancestry said none of them was in the trunk, so the board named none, and the line it did print
told the founder to remove trees one `git worktree remove` at a time.

## Rejected
- **Removing them at session start.** Deleting another tool's directory on an inference is the
  call decision 0019 leaves to the founder; one command is them making it.
- **Ancestry alone.** Squash rewrites every commit, so no squash-merged branch ever qualified.
- **`--force`.** A tree with an untracked file is somebody's; git's refusal is the check.

## Cost accepted
A branch whose work the trunk holds only inside a larger squash, in a file the trunk has changed
since, is not named (decision 0041's cost). The check reads each candidate tree, once per start.
