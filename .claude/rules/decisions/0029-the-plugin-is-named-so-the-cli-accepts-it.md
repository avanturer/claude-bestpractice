---
title: The plugin is named so the CLI accepts it, and the old id moves by the marketplace's map
paths: plugin/lib/claude_bestpractice/__init__.py, plugin/lib/claude_bestpractice/config.py, .claude-plugin/marketplace.json, install.sh, tools/check_manifests.py
date: 2026-10-04
---

## Decision
**The plugin is `bestpractice`, and only the ids Claude Code judges change:** `plugin.json`, the
marketplace entry, and `bestpractice@claude-bestpractice` where it is enabled. The repository,
the marketplace, `claude-bp-*`, `.claude/claude-bestpractice/` and the `claude-bestpractice:`
every refusal opens with keep their names.

**The old id moves by the marketplace's `renames` map.** Claude Code rewrites the old key in
user, project and local settings; a `false` under it still switches the plugin off, for the
files it does not rewrite. `install.sh` uninstalls the old id before it installs the new one.

**`make check` asks the CLI on the machine to validate both manifests**, every time.

## Why
Claude Code 2.1.289 failed `claude plugin validate` on both manifests: a name that starts
`claude-` "passes as one of Anthropic's own". It still installed and loaded, and its changelog
said nothing. In 2.1.280 a marketplace name it came to reserve went straight to refused, and one
already added stopped loading with its plugins: every gate gone, and nothing said.

## Rejected
- **Renaming everything.** Moving `.claude/claude-bestpractice/` migrates every founder's
  committed state for no rule, and a new refusal voice would hide every refusal written before
  it from the readers that find this plugin's own words in a transcript.
- **Renaming the marketplace.** `renames` maps plugins only; a new marketplace name means
  removing the old one, which uninstalls its plugins and deletes their data.
- **Listing both names.** The old entry fails validation, and both enabled run every hook twice.
- **A `migrate._REPAIRS` step.** No state of this plugin is keyed by its id, and the settings
  that are, Claude Code rewrites.

## Cost accepted
Measured on 2.1.289: after the marketplace update, `claude plugin list` shows nothing installed
and `update` answers "not installed" until `claude plugin install bestpractice@claude-bestpractice`
runs once. None of this plugin's code runs in that gap to say so; the release notes and the
README have to.
