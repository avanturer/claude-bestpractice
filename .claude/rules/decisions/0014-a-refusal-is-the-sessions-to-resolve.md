---
title: A gate's refusal is the session's to resolve, never the founder's to hear first
paths: plugin/bin/pre-tool, plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/defects.py
date: 2026-09-19
---

## Decision
Every refusal this plugin makes about something the session can act on carries one standing
instruction — work out whether the gate is right and do what it names, or decide it is wrong
and file it with `claude-bp-report defect "…"` — and the Stop gate refuses, once, a turn that
ended on such a refusal with nothing done about it.

The refusals that genuinely wait on a person carry none of it and leave nothing for the Stop
gate to ask: an unapproved merge, a production deploy, a switch that is the founder's to set.
For those, "tell the founder and stop" IS the finish (decision 0006).

## Why
The founder installed this so they would not have to read hooks. What they got instead was a
question about one: a gate refuses, the session stops, and the person who wanted a feature is
handed a paragraph about a path scanner. It happened with an SQL operator read as a redirect,
with a React variable read as a credential, and with a merged pull request reported as open —
three defects that sat unfixed because hitting them cost a conversation, and reporting them
cost another.

The session is the one that can tell those apart. It has the refusal, the call it was making
and the repository in front of it; the founder has a sentence in a chat window. So it decides,
and it keeps working either way.

The filing half is what makes the rule honest rather than a demand for obedience. A session
that must never complain and has nowhere to put a real defect learns to route around the gate,
which is worse than complaining. `claude-bp-report defect` costs the turn nothing, sends
nothing without `send`, and puts the count on a surface the founder already reads.

## Rejected
- **Asking the model nicely and nothing else.** That is a prompt, not a gate, and this
  product says so in its own non-goals.
- **Suppressing the report entirely.** A session with no way to report a wrong refusal is a
  session that disables the gate, and a disabled gate protects nothing.
- **Blocking every turn until the refusal is resolved.** One interruption per refusal is the
  whole budget, marked before it is raised, exactly like the pull-request hand-off.
- **Reading the transcript to detect complaining.** The observable fact is simpler and
  cheaper: the turn ended, and nothing happened after the block.

## Cost accepted
A session refused on its last call and legitimately out of moves is blocked one extra time
before it can stop. It costs one turn, and the message says what to do with it.
