---
title: Read what is in the repository before reporting that it is not there
paths: plugin/lib/claude_bestpractice/knowledge.py, plugin/lib/claude_bestpractice/onboard.py, plugin/bin/session-start
date: 2026-08-07
---

## Decision
**A layer in another shape is not an absent one.** `onboard.shape` answers with three
states — ours, another, none — and only the last is told to run `claude-bp init`.

**What the founder's instruction files cost is counted.** Every `.md` under
`.claude/rules/` that this plugin did not write, plus `CLAUDE.md`, is loaded into every
turn by the same harness this plugin holds itself to 400 tokens for. It is now measured
and published in `claude-bp status`.

**They are read, never rewritten.** The board tells the session to read them and to put a
new rule where the existing ones are. Nothing is reorganised, trimmed or migrated.

## Why
The plugin put its own layer in `.claude/rules/` and then judged whether a layer existed
by looking for its own four filenames. A repository with `CLAUDE.md` and eight rule files
in that exact directory was told, on every session start, that there was no knowledge
layer and to run `claude-bp init` — which from the founder's side is being told to start
what they finished months ago (#112). Three commits to those files in three days, one of
them encoding a rule that came out of this plugin's own behaviour.

## Rejected
- **Owning the founder's instruction files.** Trimming by size cannot promise that a
  standing instruction survives, and the oldest lines are often the ones they meant most.
  Decision 0005 already refused to rewrite curated documents on a hunch; this is the same
  refusal about the highest-stakes document in the repository.
- **Writing our layer beside theirs anyway.** That is a second source of truth in the same
  directory, which decision 0005 exists to prevent.
- **Staying silent about them.** They are the always-on context of every session in that
  repository and nothing was measuring them, while this plugin itemised its own to the byte.

## Cost accepted
A repository with rules in another shape gets no `claude-bp init` prompt at all, so a
founder who did want this plugin's layer has to ask for it. Better than nine files being
told they are nothing.
