---
description: Show or update the work ledger — what is done, in flight, and next
argument-hint: "[add <title> | claim <id> | done <id>]"
allowed-tools: Bash(claude-bestpractice-plan:*)
---

!`claude-bestpractice-plan ${1:-list} ${2:-} ${3:-}`

If the user asked to add a task, confirm the id. If they asked for the list, point at
the one task that unblocks the most other work, and say why in a single sentence.
