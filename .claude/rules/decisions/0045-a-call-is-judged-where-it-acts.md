---
title: A call is judged by the repository it acts on, not only where the shell stands
paths: plugin/bin/pre-tool
date: 2026-10-18
---

## Decision
**The tool-call gate judges a call in the repository where it acts.** Where the shell stands,
when that is in one; from outside every repository, the repository of the first path the call
writes or deletes; failing that, the project the session was started in (`CLAUDE_PROJECT_DIR`);
failing that, the last directory in a repository the session's transcript says it worked in.

**A call that touches no repository stays none of the gate's business**, as before.

## Why
> `rm <tree>/.claude/claude-bestpractice/failing-suite.json` went through from `/home/user`.

The gate stood down wherever the shell was not in a repository. A session left in its home
directory could delete the records no session may touch by absolute path, and its merges with
the GitHub tool were judged by nothing: 2.4.1's own release merged that way, over a red record.

## Rejected
- **Refusing every call made from outside a repository.** The plugin is installed for the user,
  and a session working nowhere near a clone would be refused for it.
- **A registry of the clones a session has worked in.** A store the session could write, kept
  to answer what the transcript and the call itself already say.
- **Judging a tool merge by its owner and repo alone.** No clone is named by them; the place the
  session worked is.

## Cost accepted
A session in a scratch directory that writes into a clone by absolute path is held to that
clone's rules, its card and its worktree among them. The transcript is the harness's format: if
it stops writing `cwd`, that last fallback names nothing, and the call is judged as before.
