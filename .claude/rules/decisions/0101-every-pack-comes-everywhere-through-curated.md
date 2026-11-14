---
title: Curated skills ship as packs, and every pack comes to every project through `curated`
paths: packs, .claude-plugin/marketplace.json, tools/check_budget.py, tools/check_shipped.py, tools/check_skills.py, tools/check_upstream.py, tools/vendor_skill.py, tools/vendored.json, .github/workflows/upstream.yml, plugin/lib/claude_bestpractice/skills.py, plugin/lib/claude_bestpractice/skillcheck.py
date: 2026-11-14
supersedes: "0100"
---

## Decision
**A curated skill ships in a pack, `packs/<name>/`, the plugin `<name>@claude-bestpractice`:**
skills only, each passing `make skills`, under 600 always-on tokens a pack and 1,200 all
together. A version's copy replaces the last whole: a skill added appears, one removed goes.

**`curated@claude-bestpractice` depends on every pack by bare name and is installed once at user
scope**, so each comes to every project; a project turns one off in its `enabledPlugins`.
**`bestpractice` depends on no pack. A pack leaves only by leaving the marketplace**, which has
`forceRemoveDeletedPlugins: true`.

**A skill from another repository is copied at a commit, never linked, from its entry in
`tools/vendored.json`:** upstream, commit, a licence that allows a changed copy and its text,
what was moved or patched. A weekly job files an issue when the upstream moves.

**A skill is admitted for a gap no first-party skill and no gate covers, and retired on its
read count, which is the founder's call.**

## Why
> зачем только для фуди поставь что бы плагин глобально эти скилы приносил

Measured on 2.1.325 in a throwaway HOME: installing `curated` put four packs in the user's
`enabledPlugins`, and an update listing one more installed it. One dropped from the dependencies
stayed until `claude plugin prune`; one dropped from the marketplace left at the next session
start with the flag and stayed, failing to load, without it. `renames: {<name>: null}` removed
nothing. A dependency that cannot load keeps its dependent disabled. `paths:` does not lower a
skill's always-on cost (~63 and ~64 tokens).

## Rejected
- **Skills inside `bestpractice`, or packs as its dependencies.** One pack off, every gate off.
- **Writing into `~/.claude/skills`, as `npx skills` does.** A second package manager.
- **A `git-subdir` link to an upstream.** It loads whatever was pushed last, unchecked.
- **`claude-seo` as a pack.** It carries a PostToolUse hook, and a pack carries none.

## Cost accepted
Every session pays for every pack, ~780 tokens by `claude plugin details`. Retiring a pack takes
it from every install, a hand-installed one too. A new version waits for auto-update or
`claude plugin update`.
