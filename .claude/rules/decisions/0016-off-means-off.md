---
title: Off means off, on the next tool call, and only the founder says it
paths: plugin/lib/claude_bestpractice/config.py, plugin/bin/pre-tool
date: 2026-09-21
---

## Decision
Two switches stand this whole plugin down: `enabled off` in `config.json`, and the harness's
own `enabledPlugins` entry set to false in the project's `.claude/settings.json`. Either one
is honoured on the NEXT tool call — `pre-tool` returns silently and `evidence-gate` lets the
turn end — rather than after a restart.

Silently, not approved: a switched-off gate has no opinion, and approving would still
suppress the permission prompt the founder would otherwise see.

Only the founder throws it. `config.json` is on `PROTECTED_STATE`. The settings file cannot
be, because a session edits permissions, hooks and env in it for them all day — so exactly
one key is guarded there, in exactly the direction that switches enforcement off, and the
refusal names their line.

## Why
A founder asked, mid-session, for the plugin to be switched off for a project. The flag went
into the project settings, the harness had already loaded the hooks, and the worktree gate
went on refusing every write and provisioning trees nobody asked for until the session ended
— while `claude-bp set require_worktree off` refused them from inside the session being
blocked (#215).

A gate with no reachable door is uninstalled, and an uninstalled gate enforces nothing. A
door that takes effect an hour later is not a door.

## Rejected
- **Standing the worktree rule down in a single-session repository.** Its whole value is
  that it holds BEFORE the second session exists; #213 is what a shared checkout costs once
  it does. The founder gets a switch instead of a heuristic about their intentions.
- **Unloading the hook.** Not ours to do — the harness owns that, and pretending otherwise
  would be a claim this plugin cannot keep.
- **Honouring the flag without guarding it.** Then a session blocked four times writes six
  characters and every gate in this repository is gone (decision 0006).
- **A prose reading of "turn the plugin off" from the founder's message.** Decision 0006
  rejected phrasing as authority for one gate; it is not better authority for all of them.

## Cost accepted
Two files are read on every tool call instead of one. Both are small, local and already in
the page cache; the alternative costs a founder a session.
