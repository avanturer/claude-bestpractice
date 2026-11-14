---
title: Every skill a session can load is listed, counted and missed when it goes, and ours say when they were checked
paths: plugin/lib/claude_bestpractice/skills.py, plugin/lib/claude_bestpractice/skilluse.py, plugin/hooks/bestpractice.ts, plugin/bin/claude-bp-mod, plugin/bin/claude-bp, plugin/bin/session-start, tools/check_skills.py
date: 2026-11-14
---

## Decision
**`claude-bp status` lists every skill a session here can load**, by source (the repository's,
the founder's, the claude.ai account synced last, each enabled plugin's), read off disk.
**Every expansion is counted under the name a session invokes it by**, and so is a `Read` of a
skill's or a command's file. Decision 0080 counted this plugin's three and nothing else.

**A skill counted here that no source lists any longer, and the skills of a claude.ai account
this machine stopped syncing, are said once on the session-start board.**

**A skill this plugin ships carries `metadata.reviewed`, and one resting on outside facts
`metadata.recheck-after-days`.** `make skills` fails a missing or impossible date and only
prints an overdue one; `status` names it.

## Why
> The plugin owns the health of every skill a session can load, not only its own.

Three synced skills went with an account change and nobody noticed for four weeks; two domain
skills were followed 17 and 2 times by reading `SKILL.md`, with no Skill-tool call to count
(#364). `landing-not-slop` called glass dated a year after Apple made it the navigation layer
of its platforms (#365).

## Rejected
- **Asking Claude Code for the listing.** The 2.1.325 API has none for a hook or a module.
- **Adding `Read` to the `pre-tool` matcher.** A Python process on every read; the module's hook
  starts one only for a skill's file.
- **Failing `make check` on an overdue date.** A calendar would hold a hotfix release.

## Cost accepted
A skill an MCP server serves, a `--plugin-dir` plugin's and one from a marketplace added by
local path are not listed. The synced account loaded is taken to be the one synced last.
