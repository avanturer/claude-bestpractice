---
title: A resumed chat's tree is its own, and a removal ends no process a session started
paths: plugin/lib/claude_bestpractice/sessions.py, plugin/lib/claude_bestpractice/worktree.py, plugin/bin/pre-tool, plugin/bin/session-start
date: 2026-11-12
---

## Decision
**A chat's record is its past once the chat has resumed in another live process after the
record last wrote**, where neither process descends from the other. The resume is the
SessionStart that says `resume`, stamped on its record (`resumed_at`). A record whose process
writes again is live again; where ancestry cannot be read, nothing is anybody's past.

**A process a Claude Code session's tool or hook ran keeps the tree it runs in**, known by
`CLAUDE_CODE_CHILD_SESSION=1` in its environment. Its removal is refused first, whether the gate
or git would do it, naming the processes and the `kill` that ends this chat's own; the Stop gate,
the sweep and an agent's tree leave it and say why. **A session's stdio MCP server is the
session**, as its CLI is: never stopped, and it keeps nothing. `CLAUDECODE=1` alone is an IDE's
terminal, the founder's, and decision 0059 stands there. This revises 0059 for the rest.

## Why
> `git -C <main> worktree remove <tree>` → refused: "… fuddy-f8 (6ae92e19) [another process of
> this chat] works there". `fuddy-f8` is this chat before the restart. … Those three background
> commands of this session then end with exit code 144.

The WSL connection dropped and the machine stayed up: the chat's old CLI ran on with nobody in
it, its `ssh` waits with it, and its records were live by every test here, the boot among them
(#310). Claude Code's env-vars reference sets the child flag "only by Claude Code itself" on its
tools, hooks and status line, "not set for stdio MCP server subprocesses"; an IDE sets
`CLAUDECODE` in its own terminals.

## Rejected
- **One harness id as one session.** A `claude -p` the chat starts shares it (decision 0032);
  it starts, never resumes, and descends from the chat.
- **Ending the old CLI.** Its record stops counting; the process is the founder's.
- **Stopping a session's command after saying so.** The `kill` named in the refusal is the
  session's own act, on what it knows it is done with.
- **`CLAUDE_CODE_SESSION_ID` as the mark.** Stdio MCP servers carry it too, and a session
  started in a tree could not remove it without killing its own server.

## Cost accepted
A tree stays while a session's dev server runs in it. On a Claude Code too old to set the child
flag, a session's command is spared and keeps nothing: git takes the directory from under it.
