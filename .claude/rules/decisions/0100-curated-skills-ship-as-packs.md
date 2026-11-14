---
title: Curated skills ship as packs, plugins of their own in this marketplace, copied and dated
paths: packs/design/.claude-plugin/plugin.json, .claude-plugin/marketplace.json, tools/check_shipped.py, tools/check_budget.py, tools/check_skills.py, tools/check_upstream.py, .github/workflows/upstream.yml, plugin/lib/claude_bestpractice/skills.py
date: 2026-11-14
---

## Decision
**A curated skill ships in a pack: `packs/<name>/`, the plugin `<name>@claude-bestpractice`,
enabled per project.** Claude Code updates it like any plugin, a version's copy replacing the
last whole, so a skill added appears and one removed goes. Nothing is written into a skills
folder of the founder's. A pack holds skills only, no hooks or servers; each passes `make skills`
and the pack's always-on stays under 600 tokens. `landing-not-slop` is the first, in `design`.

**A skill from another repository is copied at a commit, never linked**: `metadata.upstream`,
`metadata.commit`, a licence that allows a changed copy, its text beside it as `LICENSE`. A
weekly job files an issue when the upstream moves; the change comes in by pull request.

**A skill is admitted for a gap no first-party skill and no gate here covers, and retired on the
count of its reads, which is the founder's call.** A pack retired goes by `renames: {<name>: null}`.

## Why
> Как плагин должен привносить механику скилов заготовленных топовых которые мы с тобой тут
> найдем и внесём, что бы ... добавлялись новые удалялись старые у пользователя

Claude Code copies a plugin per version into its cache and drops the old copy later; plugin
skills are out of `skillOverrides`' reach, so a plugin is the unit a founder turns off; and
`paths:` does not lower a skill's always-on cost (`claude plugin details`, 2.1.325: ~63 and ~64).

## Rejected
- **Skills inside `bestpractice`.** 68 tokens of headroom, and none could be switched off alone.
- **Writing into `~/.claude/skills`, as `npx skills` does.** A second package manager, deleting
  files in the founder's home on a record of what it wrote.
- **A `git-subdir` link to the upstream.** It loads whatever was pushed last, untrimmed and
  unchecked; upstream plugins bring hooks and task lists of their own.

## Cost accepted
Each enabled pack costs its descriptions in every session there, about 60 tokens a skill. With
this marketplace's auto-update off, a pack's new version waits for `claude plugin update`.
