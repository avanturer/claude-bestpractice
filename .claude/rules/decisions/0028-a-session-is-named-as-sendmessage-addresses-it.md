---
title: A session is named the way SendMessage addresses it, asked of Claude Code at the start
paths: plugin/lib/claude_bestpractice/sessions.py, plugin/bin/session-start, plugin/bin/claude-bp
date: 2026-10-01
---

## Decision
**Every place the plugin names a session puts the name `SendMessage` takes beside its id**: the
board, `claude-bp status`, a card's owner and notes, a pull request's row, and every refusal or
note that sends one session to another.

**The name comes from `claude agents --json`, the interface Claude Code supports for reading
session state from outside a session.** It is asked once per session start and per
`claude-bp status`, never on a tool call, with five seconds to answer. No answer means no names,
never a refusal.

**A record whose process that answer lists under another id is reaped.** That is a conversation
`/clear` or `/resume` left in a running CLI, and it counts only for a pid that is the CLI
itself, read in this pid namespace.

## Why
> Везде, где плагин называет сессию (SESSIONS, `owner` задачи, `claimed by`, PR, worktree),
> рядом стоит её имя для `SendMessage`.

Three live chats, and the only way to tell which one to write to was matching files under
`~/.claude/sessions/` by hand (#253). No hook payload carries the name, and `/rename` or an
accepted plan changes it, so it is read where the board is built.

## Rejected
- **Reading `~/.claude/sessions/<pid>.json`.** It has the name, in an undocumented format.
- **Asking on every tool call.** A third of a second per call is how a gate gets switched off.
- **Keying records by the name.** It changes; ids are what the ledger and the records match on,
  so the name is shown, never matched.

## Cost accepted
A rename reaches the board at the next session start or `claude-bp status`, not at once.
