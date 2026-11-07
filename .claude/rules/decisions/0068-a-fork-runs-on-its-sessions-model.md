---
title: A fork runs on its session's model, so its spawn is not asked for a tier
paths: plugin/lib/claude_bestpractice/subagents.py, plugin/bin/pre-tool
date: 2026-11-06
---

## Decision
**An `Agent` call with `subagent_type: "fork"` is not refused for naming no model.** Claude Code
runs a fork on the session's own model and ignores `model` on the call, because the fork reads
the parent's prompt cache: nobody defaulted into anything. A fork still counts toward
`subagent_fanout`, as one more agent at work. Decision 0015 holds for every other spawn.

## Why
> изучи что такое самый новые deepseek harness о котором говорят, самые современные статьи и
> плагины для клод кода vscode, что бы взять все лучшее и исправить неполноценное или
> неоптимальное к нам в плагин

Forks are on by default in the terminal since Claude Code 2.1.232, and in VS Code, the desktop
app and `claude -p` since 2.1.302. The tier rule looked a fork up as an agent named `fork`,
found no definition pinning a model, and refused it: the session added a `model` the harness
then discarded and spawned again, a round trip on every fork. The hooks module already let
forks pass (decision 0051).

## Rejected
- **Asking a fork for a tier anyway.** The field is thrown away; the refusal buys nothing.
- **Sparing forks the fan-out ceiling too.** A fork reads the parent's cache cheaply, and still
  calls tools and writes output beside the others.
- **Any type with `fork` in its name.** An agent of the founder's named `forked-review` is an
  agent like any other.

## Cost accepted
A fork runs on the most expensive model in the room, unasked. It never could run on another.
