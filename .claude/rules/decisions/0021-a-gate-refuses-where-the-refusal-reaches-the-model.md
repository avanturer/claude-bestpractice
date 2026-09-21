---
title: A gate refuses only where the refusal reaches the model
paths: plugin/bin/checkpoint, plugin/bin/evidence-gate
date: 2026-09-21
---

## Decision
**A gate may refuse only at an event whose refusal reaches the model and gives it a turn.**
Where the harness hands the refusal to the FOUNDER instead, the gate does not refuse: it
stays silent and the demand moves to an event that does. `PreCompact` is the first event
named here, and `claude-bp-doctor` runs a manual compaction and fails if anything cancels it.

**Which events those are is measured, never read off a table.** "Can block: yes" is
documented; what the block then does is a separate question, and only the CLI answers it.

## Why
The checkpoint blocked one compaction per session to make the window write down what only
it knew. Measured on 2.1.278, both routes — `exit 2` and `{"decision": "block"}` — end the
turn with `num_turns: 0`, zero tokens in and out, and the text delivered as a `<synthetic>`
assistant message. No model call happens at all.

So the one interruption a session is allowed was spent on an instruction addressed to the
model and read only by the founder, whose `/compact` was cancelled with the custom
instructions they had typed into it, and who then typed the whole thing again. Three
releases spent fixing its wording could not have helped: the reader was never there. By
decision 0014 a refusal is the session's to resolve, and this one never could be.

## Rejected
- **Rewriting the block for the founder.** Honest, and still a gate that spends their
  command to ask them to do something by hand — the thing they installed this to stop.
- **Keeping it because the event is listed as blocking.** It blocks. The block cannot be
  answered, and "can block" is not "can be answered".
- **Asking near the context limit instead.** The window size is not a fact a hook holds,
  and estimating it from an internal transcript format buys a guess and a second mechanism.

## Cost accepted
The ask lands at a clean finish past `notes_after_calls` tool calls rather than at the
compaction — earlier, on a threshold that is a judgement, and it costs the turn it holds
open one more run of the suite. A session that compacts before it is asked keeps what it
had: a checkpoint, its card, and nothing else.
