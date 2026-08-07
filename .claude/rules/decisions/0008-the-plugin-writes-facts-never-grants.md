---
title: The plugin holds the pen on facts about this repository, never on grants
paths: plugin/lib/claude_bestpractice/policy.py, plugin/bin/setup, plugin/bin/claude-bp
date: 2026-08-07
---

## Decision
**Facts are generated, grants are not.** `autoMode.environment` says what this repository
is — path, remotes, trunk, checks, that sessions share one clone through worktrees. All of
it is re-derived from the repository on every run. `autoMode.allow` widens what may proceed
unattended and is never written here.

**Only marked entries are touched.** Every generated line carries `[claude-bestpractice
<repo path>]`. Entries without it — the founder's prose, another repository's block, keys
this module has never heard of — are carried through unchanged.

**The agent runs it, not the founder.** `claude-bp policy --apply`, and `Setup` once per
project. Safe because the content comes from the repository rather than from anything the
agent says, so running it cannot widen what may proceed.

**Rules that have gone dead or stale are reported.** Never deleted, never rewritten.

## Why
Two layers answer "may this proceed unattended" — the classifier from prose in
`~/.claude/settings.json`, this plugin from state it computes — and the only thing joining
them was the founder retyping one half into the other. 8,940 bytes of hand-written policy
on one machine, most of it authored mid-session, at the moment a prompt had already
interrupted something else, which is the worst possible incentive for a permission rule.
Eight worktree entries, six inert and two unable to fire at all, with nothing telling him
which. An environment rule still naming a production key that had been moved and revoked
the day before (#113).

## Rejected
- **Generating grants too.** A session that has just been interrupted has a direct motive
  to widen what may proceed. Derived facts cannot be steered by an agent; a grant can.
- **Block markers around a region.** Per-entry markers survive the founder editing beside
  them and let two governed repositories be refreshed independently.
- **Writing on every session start.** A global file rewritten by every one of eight live
  sessions, several times an hour, to say the same thing.
- **Generating a block in `CLAUDE.md` as well.** Session start already delivers those facts,
  fresher and cheaper; a second copy would double the always-on cost to say it twice.

## Cost accepted
A project-scoped plugin writes a machine-wide file. Bounded by the marker, scoped by path
in every line, and re-derivable — so the worst case is a stale sentence about a repository,
which is what this replaced.
