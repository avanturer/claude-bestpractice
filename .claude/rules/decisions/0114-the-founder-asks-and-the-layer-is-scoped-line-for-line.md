---
title: The founder asks, and their instruction layer is scoped line for line
paths: plugin/skills/scope-rules/SKILL.md, plugin/bin/claude-bp, plugin/lib/claude_bestpractice/rulescope.py, plugin/bin/session-start
date: 2026-11-23
---

## Decision
**Decision 0007 holds for everything this plugin does unasked. A skill the founder runs,
`/bestpractice:scope-rules`, may move their instruction text between files**: verbatim, with
`paths:` and `commands:` frontmatter, a one-line invariant left where a section meant for every
session was, and a routing line in `CLAUDE.md` per moved topic. `disable-model-invocation`, so
only the founder starts it and it costs no context until then. It ends on two checks:
`claude-bp rules --since <base>` names every line in no file any more, every line that left the
always-on layer (flagged where it reads as meant for every session), every line moved away from
its heading, every glob that matches nothing tracked and every broken `→ «heading»` reference;
`claude-bp rules --probe` has headless sessions read one file per rule and finds the rule in
their transcripts. `status` counts tokens from bytes, the founder's `~/.claude/rules` and
MEMORY.md as loaded included, and names MEMORY.md past what loads.

## Why
> как мы обновим плагин агент которого я попросил это сделать все приберет и сделает правильно

An agent asked to "scope the rules" had nothing to follow and nothing to check it against. The
independent review of the first plan found a line-presence check proves the text exists, not
that the rule still applies where it was meant to.

## Rejected
- **Migrating on upgrade, through `migrate._REPAIRS`.** The founder's words, rewritten unasked:
  what 0007 refused, for the reason it gave.
- **Trimming or summarising.** The oldest lines are often the ones meant most (0007).
- **Counting characters, a third to a token.** Cyrillic is near half that: understated by a third.

## Cost accepted
A probe is a model call per rule. Lines a migration deletes on purpose need the founder's word.
