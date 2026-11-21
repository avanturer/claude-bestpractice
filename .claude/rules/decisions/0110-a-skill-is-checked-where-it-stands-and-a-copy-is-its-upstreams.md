---
title: A skill is checked where it stands, and a copy with its own licence is its upstream's
paths: plugin/lib/claude_bestpractice/skillcheck.py, plugin/lib/claude_bestpractice/skilluse.py, plugin/lib/claude_bestpractice/skills.py, plugin/lib/claude_bestpractice/migrate.py, plugin/bin/claude-bp
date: 2026-11-21
---

## Decision
**A script or a path a skill names is looked for in the repository and in the skill's own
folder**, and a `./` or `../` path from the folder. **A skill in the founder's folder with its own
licence beside it is a copy of someone else's and is not checked.** A card a check filed whose
skill has no finding now is withdrawn at the next session start (repair 0053 for the ones filed
before this). **A read is counted only for a skill a session here can load**, and **every skill
that dates itself is named once due**, a pack's included. `claude-bp skills` lists each loadable
skill with its source and how it was read. Decision 0099 holds otherwise.

## Why
> 11 of the 17 findings are false positives.

Seven scripts sat in the skill's `scripts/`, four `../` paths passed a guard `..` always passes,
and the rest were steps of copied upstream skills: nine cards in each of two repositories. A
read of a pack's source in this repository was counted as a skill nothing lists, and called gone
(#364). The pack skills' recheck dates were read from `plugin/skills` only, so none was ever due.

## Rejected
- **Checking the copies anyway.** The founder fixes them by editing a copy the upstream overwrites.

## Cost accepted
A founder's own skill with a licence file in its folder goes unchecked.
