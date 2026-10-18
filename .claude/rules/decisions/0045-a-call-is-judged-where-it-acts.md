---
title: A call is judged by the repository it acts on, not only where the shell stands
paths: plugin/lib/claude_bestpractice/hookio.py, plugin/bin/pre-tool, plugin/bin/pr-opened, plugin/bin/prompt-capture
date: 2026-10-18
---

## Decision
**A call is placed in the repository where it acts**, by the gate that judges it and by the
hooks that keep what it did and what the founder said. Where the shell stands, when that is in
one; from outside every repository, the tree of the first path the call writes or deletes;
failing that, the project the session was started in (`CLAUDE_PROJECT_DIR`); failing that, the
last directory in a repository the session's transcript says it worked in.

**The session is known there by that tree**, as inside it. **A directory gone from disk is read
as the tree that holds it**: a removed worktree as its clone, a directory a write is about to
make as its repository, and a place inside `.git` as its working tree.

**A call that touches no repository stays none of the gate's business**, as before.

## Why
> `rm <tree>/.claude/claude-bestpractice/failing-suite.json` went through from `/home/user`.

The gate stood down wherever the shell was not in a repository: records no session may touch were
deleted by absolute path, and merges with the GitHub tool were judged by nothing, 2.4.1's own
release among them. 2.5.1 judged such a call and nothing else looked: there the session was the
bare harness id, holding no word said in its tree, the founder's `+merge` typed there was dropped,
#279 merged from there stayed open, and a removed tree named no repository, so calls went unjudged.

## Rejected
- **Refusing every call made from outside a repository.** The plugin is installed for the user,
  and a session working nowhere near a clone would be refused for it.
- **A registry of the clones a session has worked in.** A store the session could write, kept
  to answer what the transcript and the call itself already say.
- **Judging a tool merge by its owner and repo alone.** No clone is named by them; the place the
  session worked is.
- **Placing the Stop gate too.** It runs the suite of the tree it judges, and a turn ended in
  `/home/user` would run the suite of a clone the session may only have read.

## Cost accepted
A session in a scratch directory that writes into a clone by absolute path is held to that
clone's rules, its card and its worktree among them. A session that removed its tree is placed in
the clone's main checkout, where the records it kept in that tree are not its own. If the harness
stops writing `cwd` in the transcript, that fallback names nothing.
