---
title: A gate is switched by the founder's word, never by the session it enforces
paths: plugin/lib/claude_bestpractice/config.py, plugin/bin/claude-bp, plugin/bin/prompt-capture
date: 2026-08-07
---

## Decision
**Every gate switch has one door: `claude-bp set <key> <value>`.** No gate names
`config.json` as its remedy any more, because the write hook refuses that file to the
session being enforced.

**The key to that door is the founder's own message.** `prompt-capture` records a switch
they asked for in their own words — `scope_drift_block off` — where no session can write
it, and `set` refuses without a matching one. The word is consumed on use.

**`test_command` and the artifact keys are not settable this way at all.** They decide
whether a finish is verifiable, and no command sets them: `test_command` is detected from
the project, and where detection is wrong the founder edits `config.json` directly — the
file `pre-tool` refuses to sessions, which is what makes it theirs.

This paragraph used to name a "claude-bp ci" command as their owner. No such command exists,
the sentence had been copied into two refusals and a comment, and a session reading this
record for authority was sent to something that exits 2. Removed, not rewritten: nothing
replaced it.

## Why
`evidence-gate` blocked a turn and offered, as one of two ways out, setting
`scope_drift_block` in a file that `pre-tool` refuses in the same breath — so the founder
was read a remedy out loud and then told the assistant could not perform it, which reads
as the assistant being unhelpful rather than the plugin contradicting itself (#108). Seven
messages named that file. The threat model has not moved: a session blocked four times is
exactly who reaches for the switch, so a door it can open alone is a gate that turns itself
off.

## Rejected
- **Letting the session write the workflow keys.** `scope_drift_block` was argued to be a
  preference rather than enforcement state. It blocks a Stop; anything that ends a blocked
  turn is enforcement state.
- **Founder-only, outside a session.** The founder is often on a machine that has no
  terminal on this repository — the chat is the whole interface. A remedy they cannot
  reach is the defect, one layer along.
- **Reading intent from their prose.** A regex judging language would be a gate switched by
  phrasing. The literal this plugin printed for them to repeat is checkable.

## Cost accepted
Two turns instead of one: the founder says the word, the session runs the command. That is
the price of the switch not being reachable by the thing it governs.
