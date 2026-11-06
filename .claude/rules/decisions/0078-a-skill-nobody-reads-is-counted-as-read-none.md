---
title: How often each of this plugin's skills is read is counted, and a skill nobody reads shows it
paths: plugin/lib/claude_bestpractice/skilluse.py, plugin/hooks/bestpractice.ts, plugin/bin/claude-bp
date: 2026-11-06
---

## Decision
**The hooks module counts each expansion of one of this plugin's skills** — through the Skill
tool, a slash command or a preload — **and `claude-bp status` says what each came to, since the
count began** at a session start, so a skill never read shows as read none. Counted, never acted
on: whether a skill stays is the founder's call, on the number.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

A skill loads only when the model chooses it, and its description rides in every request
either way: about a third of this plugin's always-on tokens. JetBrains tested the Ponytail
ruleset as a skill alone (July 2026): "Across all ten sessions it self-activated zero times";
it ships as a SessionStart hook for that reason. Claude Code's skills page says a rule that must
always hold belongs in a hook. `DESIGN.md` §9 deletes a gate with no true positive in sixty days,
and nothing counted a skill's.

## Rejected
- **Removing the skills now.** Ten sessions of another plugin's skill is not this one's number.
- **`claude plugin eval`.** Paid model runs on tasks someone wrote, against what the founder's
  own sessions do for free.
- **A PreToolUse match on `Skill`.** A slash command or a preload never calls the tool.

## Cost accepted
A Python process per expansion of one of these skills. A session started before this version
counts nothing until it is restarted.
