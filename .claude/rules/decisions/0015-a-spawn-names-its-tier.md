---
title: A spawn names the tier it runs as, and the count is the founder's number
paths: plugin/lib/claude_bestpractice/subagents.py, plugin/bin/pre-tool, plugin/hooks/hooks.json
date: 2026-09-19
---

## Decision
An `Agent` call is refused unless a model is named — on the call, or pinned in that agent's own
definition — and a turn may start at most `subagent_fanout` of them, three by default.

`inherit` counts from a definition and not from a call. In a file it is a decision somebody
wrote down; on a call it is the default spelled out.

## Why
Claude Code resolves a subagent's model from the call, then the definition, then
`CLAUDE_CODE_SUBAGENT_MODEL`, then the parent session. A call that names nothing therefore runs
on the parent's model, which in these sessions is the most expensive one available — and the
three agents a session reaches for without thinking, Explore, Plan and general-purpose, have no
definition to pin anything. A search for a filename runs on the model that was chosen for
architecture, several times over, and nothing records that a choice was made because none was.

The count is the other half and the more expensive one. Each agent pays for its own context of
the repository before it reads a line of the answer, so a fan-out is the costliest way there is
to go slightly faster, and agents searching one tree return one answer several times.

Asked at `PreToolUse` because that is the only place it can be asked: `SubagentStart` cannot
block a spawn and is handed neither the model nor the prompt, so by the time it fires the
choice has been made and paid for.

## Rejected
- **Demanding a written justification for the tier.** No gate can tell a true reason from a
  plausible one, and demanding a sentence buys a sentence. The tier guide goes in the refusal
  and in the skill, which is where a reason belongs.
- **Picking the tier for the session.** The plugin does not know what the work is. It knows
  that nobody chose.
- **A ceiling the session can raise.** `subagent_fanout` is on `PROTECTED_STATE` like every
  other switch: a gate the gated party can widen is a suggestion (decision 0006).
- **Recording what each spawn actually cost.** `PostToolUse` carries `totalTokens` and
  `resolvedModel`, and reading them would spend the last hook entry in the budget on a number
  nothing enforces.

## Cost accepted
A founder who wants unbounded fan-out sets `subagent_fanout 0`, and every call to a built-in
agent carries one more field than it did. The field is four characters and the measurement it
replaces is a bill.
