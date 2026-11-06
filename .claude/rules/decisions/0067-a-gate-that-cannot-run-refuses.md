---
title: A gate that cannot run refuses the call
paths: plugin/hooks/hooks.json, plugin/bin/pre-tool
date: 2026-11-06
---

## Decision
**The `pre-tool` entry says `"onFailure": "block"`**, so Claude Code refuses the call when the
gate cannot start, dies before `hookio.guard` holds it, or outruns its fifteen seconds, as the
guard already refused for any failure inside it. Honoured from Claude Code 2.1.295. No other
entry carries it: the Stop gate and the commit reviewer run in the background, where Claude Code
ignores it, and the rest refuse nothing.

## Why
> изучи что такое самый новые deepseek harness о котором говорят, самые современные статьи и
> плагины для клод кода vscode, что бы взять все лучшее и исправить неполноценное или
> неоптимальное к нам в плагин

Claude Code runs the tool on any exit but 2, and documents it: "a policy hook with a wrong path
or a crashing script then lets everything through." The manifest's protocol note was written
over one such failure, an install path with a space that made every gate exit 127: the session
ran with no gate at all and nothing said so. A gate that cannot run said "allowed" in the one
way nobody reads.

## Rejected
- **Leaving it to `guard`.** It cannot catch an import that fails before it is called, a missing
  interpreter, or the harness killing the gate at its timeout.
- **Failing `session-start`, `prompt-capture` or `checkpoint` closed.** They refuse nothing, and
  a lost board or checkpoint is not worth a refused session.

## Cost accepted
Where the gate cannot run, a missing `python3` among them, every write, edit and shell call is
refused until the founder repairs it, or switches the plugin off and starts the session again:
`enabled off` is read by the gate that cannot run. That is the posture `pre-tool` always claimed.
