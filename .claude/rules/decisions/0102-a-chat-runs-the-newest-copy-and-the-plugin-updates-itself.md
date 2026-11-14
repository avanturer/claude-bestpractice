---
title: A chat runs the newest installed copy's gates, and the plugin brings its own updates to disk
paths: plugin/lib/claude_bestpractice/upgrade.py, plugin/lib/claude_bestpractice/selfupdate.py, plugin/bin
date: 2026-11-14
---

## Decision
**Every command of an old copy hands over to the newest copy installed beside it**
(`upgrade.handover`, first in each script of `bin/`): the same stdin, arguments and status,
`CLAUDE_PLUGIN_ROOT` naming the new copy, `CLAUDE_BESTPRACTICE_LOADED_FROM` the old. A chat
started before an update is gated by the version on disk. Not to a copy the CLI marked
`.orphaned_at`, and not with `CLAUDE_BESTPRACTICE_NO_HANDOVER=1`.

**A session's start sets off the plugin's own update, at most once in six hours on the
machine**, in a process nobody waits on: the CLI refreshes this marketplace, updates each of its
plugins installed for the user or for this project, and installs `curated` once where it never
was. Not where Claude Code's update switches are off, this marketplace's `autoUpdate` is false,
`CLAUDE_BESTPRACTICE_NO_SELF_UPDATE=1`, or no `claude` is on the PATH.

**What Claude Code loaded for a chat waits for a new chat or a `/reload-plugins` the founder
types**: the skills and commands it lists, `hooks.json` and the hooks module. The chat is told
so once.

## Why
> я не хочу сам ничего выполнять все должно было обновиться в этом чате и всем соседним
> активным чатам при обновлении сессия должна рассылать выполнить /reload-plugins

Measured on 2.1.325: `$.command.run({ command: "reload-plugins" })` answered "/reload-plugins
isn't available over a remote connection in this session", and `$.prompt.submit` of it was
refused as a command run as the user. No session can reload another, or itself. An old copy's
`claude-bp-plan --version` printed the newer copy's version after `claude plugin update`.

## Rejected
- **Sending `/reload-plugins` to the other chats.** Claude Code refuses it from anything but the
  founder's keyboard.
- **Writing `autoUpdate: true` into the founder's settings.** It installs nothing missing.
- **`bestpractice` depending on `curated`.** One pack that cannot load would disable every gate.

## Cost accepted
A hook of a stale chat starts one more Python on Windows. A new copy's commands run under the
old copy's `hooks.json` until the chat reloads. The plugin updates itself from its main branch
without asking, as Claude Code's own auto-update would.
