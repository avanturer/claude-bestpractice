---
title: A scoped rule is in the conversation before the act it governs
paths: plugin/lib/claude_bestpractice/rulescope.py, plugin/bin/pre-tool, plugin/bin/checkpoint, plugin/hooks/bestpractice.ts, plugin/bin/claude-bp-mod
date: 2026-11-23
---

## Decision
**A write whose path a rule's `paths:` matches, or a command its `commands:` matches, is refused
once while that rule is not in the loop doing it**, main conversation and each subagent apart.
The hooks module reports a load: `prompt.attachment` of type `nested_memory`, which names the
rule's absolute path, and a `Read` of a rule file. `checkpoint` forgets a loop's rules at
compaction. `commands:` is this plugin's field; Claude Code reads only `paths`. Silent until the
module says it runs for the session. `rules_in_context off` stands it down.

## Why
> что бы модель на нужной задаче сама доставала нужное

Measured on the founder's product repository: about 46,800 tokens of rules in every turn, two
thirds of them about one area each. Scoping them is what Claude Code offers, and it has three
holes: a new file is written before its rule loads, a plan from an Explore subagent's summary
carries none of the rules the subagent saw, and a release, `ssh` to production or SQL on the live
database touches no file. Scoping without closing them trades tokens for missed rules.

## Rejected
- **An `InstructionsLoaded` hook entry.** Documented, but the twelfth and last entry (0019,
  0112), and a process per load. The module's event costs neither, and carries the loop's id.
- **Reading the transcript for attachments.** It may lag the call, and its format is not stable.
- **Refusing until the rule is loaded.** A signal the module missed would wall the session.
- **Matching globs in the module.** Two implementations of Claude Code's globs, drifting apart.

## Cost accepted
One refusal per scoped rule per loop where the module's report did not arrive, and after each
compaction for the rules a loop needs again. Reads of the rule's file in a Code Mode script are
seen per call (0103); whether Claude Code attaches a rule to such a read is not documented.
