---
title: A finished tree removes itself, and what the plugin may not remove it names
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/evidence-gate, plugin/bin/session-start
date: 2026-09-21
---

## Decision
**The session that finishes the work puts the tree away.** On the turn the Stop gate allows, a
worktree this plugin provisioned for this session is removed when all four hold: its branch's
work is in the trunk, this session has closed every card it holds and closed at least one,
`git status` in it is empty including untracked files, and it is not the main checkout. The
branch goes with it — `-d`, or `-D` only where every file it delivers is provably
byte-identical to the trunk, which is what a squash merge leaves behind.

**Nothing is asked, and nothing else is touched.** A tree another tool created is never
removed; it is named on the board with the command that removes it. So is a branch carrying
commits no pull request here has seen, and a pull request nothing has moved for a week.
`remove_finished_trees: false` turns the removal off and leaves the naming.

## Why
> когда из ворктри уже все замерджили и модель даже ВСЕ свои задачи закрыла то она сама его
> удаляла, так ничего мы не теряем и меня не будет тыркать она с разрешением

Measured where it was asked: thirty-eight worktrees besides the main checkout, fifteen over an
already-merged branch, and a hundred and thirty-five local branches of which thirty-one were
merged and undeleted. "Remove your tree after the merge" was in that project's own instructions
the whole time — an instruction is not a mechanism. The conditions above are facts, and the act
is a command that REFUSES: `git worktree remove` without `--force` will not touch a tree with a
modified or untracked file, so they decide only whether to ask git.

## Rejected
- **Asking first.** The thing the founder asked to stop, and why fifteen trees were still there.
- **Only sweeping dead sessions' trees.** That existed and left the pile: they accumulate while
  the sessions are alive, and that sweep runs when somebody else starts.
- **`-D` on any branch git refuses.** One proof only, and content identity is it.
- **Removing trees this plugin did not provision.** Deleting another tool's directory on an
  inference is not a plugin's to make. Named instead.
- **A SessionEnd hook.** Tidier, and it spends the twelfth and last hook entry on what the Stop
  gate already knows: every turn ends in one.

## Cost accepted
A session that finishes one task and starts another gets a NEW tree, so its setup runs again.
Accepted: that tree is cut from a trunk that has moved on, and the one it replaces is the stale
tree that made a suite red on code nobody present wrote (#217, #220).
