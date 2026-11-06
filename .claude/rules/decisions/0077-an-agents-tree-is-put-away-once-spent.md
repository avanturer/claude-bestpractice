---
title: The tree a chat's agent ran in is put away once it is spent
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/bin/evidence-gate
date: 2026-11-06
---

## Decision
**A Stop the gate lets end, with none of the chat's agents still at work, removes the trees
`worktree-create` made for that chat's agents** where nobody stands in them, `git status` is
empty and the branch's work is in the trunk: `git worktree remove` without `--force`, and the
branch only on proof, as decision 0019 removes a session's own tree. `remove_finished_trees`
switches it off with the rest.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

Claude Code's hooks reference: "When a subagent or a workflow agent finishes, Claude Code keeps
the worktree your WorktreeCreate hook made for it and doesn't run your WorktreeRemove hook.
Remove those worktrees yourself when the work is done." The hook records such a tree for the
chat's harness id and no session, so the reaper never reads it, and only `claude-bp sweep`
asked by the founder took it: the pile of #220, one agent at a time.

## Rejected
- **A WorktreeRemove hook.** A thirteenth entry, and Claude Code does not run it for these.
- **Removing them at session start.** Another chat's agent may be in one; the chat whose agent
  it was knows when its agents are done.
- **An agent's committed work that never reached the trunk.** It stays, branch and all, for the
  chat to merge or the founder to drop.

## Cost accepted
A tree an agent left clean and level with the trunk goes, though the chat meant to send another
agent into it; the next agent gets a new one.
