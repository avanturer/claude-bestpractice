---
title: A subagent runs no lower than the founder's floor
paths: plugin/hooks/bestpractice.ts, plugin/lib/claude_bestpractice/subagents.py, plugin/lib/claude_bestpractice/config.py
date: 2026-10-30
---

## Decision
**A spawn that names a tier below `subagent_min_model` runs on that tier**, `sonnet` unless the
founder lowers it. The call still names a tier (decision 0015), and one named at or above the
floor runs as named. `subagent_min_model off` lets every spawn run on what it names.

## Why
> поставь лучше сонет и дай ему закончить задачу хайку тупой

A research agent on Haiku looped on one fetch until it hit its output limit. Re-run on Sonnet,
it finished. The tier guide had told sessions to pick Haiku for "search", which is where it failed.

## Rejected
- **Refusing a spawn that names Haiku.** A round trip for a call the floor can simply raise.
- **Raising the agents whose definition pins a tier.** That is a decision somebody wrote down.

## Cost accepted
A lookup that Haiku would have answered costs a Sonnet run.
