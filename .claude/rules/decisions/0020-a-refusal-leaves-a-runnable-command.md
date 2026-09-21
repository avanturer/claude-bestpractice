---
title: Every refusal leaves at least one command that runs on this machine
paths: plugin/bin/claude-bp-doctor, plugin/bin/pre-tool
date: 2026-09-21
---

## Decision
**A refusal that names no command a session can run right now is a defect, and the doctor
proves it by provoking refusals rather than by reading strings.** Check 35 fires three of
them — one through `deny`, one through the Stop gate's block — reads every command each
names, and fails when a named program does not exist on this machine.

## Why
> у каждого отказа обязана существовать хотя бы одна команда, которую сессия может выполнить
> прямо сейчас. Если её нет — это не гейт, а тупик.

Three in one day, all shipped, all passing every check that existed: #216 said to write the
database into `.env`, which the same gate blocked, and named `claude-bp database`, which died
on a missing `psql`; #218 said to run a removal in the tree being removed, which git refuses;
#217 had two gates requiring opposite things. Each was a gate that looked actionable in its
own test and was a dead end in the repository.

The invariant is checkable, which is the whole reason to state it this way: a refusal is
text, the commands in it are extractable, and whether a program exists is a fact about the
machine. Its first run found a dead end — in the checker, reporting one where an exit
existed — and that is the direction a checker is allowed to be wrong in once.

## Rejected
- **A rule in a document.** That is how the three defects shipped: every one of them was
  against a rule already written down.
- **Grepping the gate sources for a backtick.** A refusal is composed at runtime from
  several modules; the string in the source is not the string the session reads.
- **Failing a gate that cannot name a remedy.** Some refusals genuinely wait on a person
  (decision 0014, decision 0006), and for those "tell the founder and stop" IS the exit.
  What is refused is the dead end: no exit at all, or one that cannot run here.

## Cost accepted
The doctor takes a few seconds longer, and a refusal whose remedy is a tool rather than a
shell command has to name it explicitly — `EnterWorktree` is on a short list here for that
reason.
