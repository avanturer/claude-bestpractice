---
title: A defect reported outside every repository is kept on the machine
paths: plugin/lib/claude_bestpractice/defects.py, plugin/bin/claude-bp-report, plugin/lib/claude_bestpractice/hookio.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-11-21
---

## Decision
**`claude-bp-report` runs outside every repository**: a report made there, by a session or by
a crashed gate, is kept in the plugin's own directory on the machine (`selfupdate.data_dir`),
and `list`, `send` and the board of every clone read it beside their own. A sent row is written
beside the report it marks. One send lock for the machine. Mode there is decision 0092's rule:
`auto` where `gh` files as the owner, `local` elsewhere.

**A `.git` git would not read is no tree** (`hookio.is_a_git_dir`): a directory with `HEAD`, or
a `gitdir:` file. Repair 0052 takes this plugin's state out of one above a clone, and the `.git`
with it where nothing else is left but Claude Code's runtime excludes.

## Why
> Если гейт сработал вне git-репозитория, названный им же способ сообщить о дефекте тоже
> работает вне git-репозитория.

A session in a folder that is no repository was refused, told to run `claude-bp-report defect`,
and the command answered "not inside a git repository" (#379). On the founder's machine, state
written before #187 was fixed had made `~/.git`: every folder under the home read as a tree,
the gates placed calls there in the folder itself, and Claude Code took the home for a
repository.

## Rejected
- **The refusal naming another way to report.** The command it names is the one that has to work.
- **Finding the session's clone from the CLI.** Claude Code gives the Bash tool no transcript.

## Cost accepted
Reports made outside every repository appear on every clone's board until sent.
