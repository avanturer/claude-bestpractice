---
title: Code Mode is the founder's switch, and a script is judged call by call at PreToolUse
paths: plugin/hooks/hooks.json, plugin/bin/pre-tool, plugin/bin/pr-opened, plugin/hooks/bestpractice.ts, policy/managed-settings.example.json
date: 2026-11-20
---

## Decision
**Code Mode (`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`, 2.1.323) is switched on by the founder, in
`~/.claude/settings.json` or the shell, never by this plugin**: a project's settings cannot, and
managed settings only keep it off.

**A script's calls are judged by `pre-tool` one by one**, as each meets `deny`, `ask` and
PreToolUse under its own name. `"onFailure": "block"` keeps a gate that cannot run refusing them
since 2.1.329, and no gate answers with `updatedInput`, after which auto mode runs no script.

**Nothing is rebuilt from `REPL`'s own PostToolUse, and the hooks module stays as it is.** Where
neither reaches a call inside a script, `pr-opened` keeps nothing of it and each falls back as a
missed PostToolUse does (decision 0038). No refusal moves: a query's credential scan is `pre-tool`'s.

## Why
> нужно ли нам что то менять что бы спокойно включить и всегда работало без ущерба всем
> запросам этот режим

Read on 2.1.329 (its binary, the Code Mode page, the hooks reference, the changelog): PreToolUse
runs per call of a script; PostToolUse is documented for `REPL` alone, whose `tool_response` names
the tools used (`toolsUsed: ["Bash"]`), not what each returned. Code Mode starts in no cloud
session, behind no gateway, under no `-p`, SDK, Desktop or VS Code, so nothing here can measure it.

## Rejected
- **Learning a number from a script's output.** `/pull/<n>` there may be another pull request's,
  and `learn_number` given a different one settles the live record.
- **Settling a merge when its script succeeded.** A script can catch a failed `gh pr merge`, and
  the word goes as it did before decision 0038 (#232, #243, #268).
- **`REPL` in `pre-tool`'s matcher.** A process per script, for calls judged one by one anyway.
- **Writing the switch from `setup` or `install.sh`.** An experimental mode, in the founder's file.

## Cost accepted
Inside a script, until a terminal session measures it: a pull request opened may reach the board
without its number, a merge or close settles at `reconcile`, an agent stopped is on no paused card,
a dialog answered grants no word, a long output is not digested and a search is not routed.
