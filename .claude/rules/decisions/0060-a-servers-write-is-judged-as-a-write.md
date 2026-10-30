---
title: A server's tool that writes files is judged as the write it is
paths: plugin/lib/claude_bestpractice/servers.py, plugin/bin/pre-tool, plugin/hooks/hooks.json
date: 2026-10-30
---

## Decision
**A tool an MCP server offers that writes files is judged as the harness's own `Write` of the
file it names**, and a move as `mv` of both ends: the card, the lease, the protected files and
the tree are asked of it as of any write. The reference filesystem server's `write_file`,
`edit_file`, `create_directory` and `move_file`, and Serena's editing tools, by name.

**The gate never approves one in its server's place.** A call it lets through keeps the
permission prompt the founder set for that server's tools.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

The plugin's documentation already said why not to connect such a server: its tools write files
past every gate. A rule that a session can walk past by calling another tool is a suggestion,
and a session told it may not write `config.json` was one tool away from writing it.

## Rejected
- **Refusing every server tool that writes.** Serena's symbol edits are what a project connects
  it for; refused, it is disconnected, and its reads go with it.
- **Matching any tool whose name says write or edit.** A server's `edit_issue` writes no file
  here, and a refusal over it is a gate that cannot be reasoned about.
- **Serena's shell and REPL tools.** Code run there names no file; they are the server's own
  prompt to keep, as `Bash` was before its lines were read.

## Cost accepted
A server whose tools are named otherwise writes unjudged, as before. A Serena edit that renames a
symbol across files is judged on the file it names.
