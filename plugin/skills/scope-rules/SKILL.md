---
name: scope-rules
description: "Use when every turn carries instruction files only some work needs (`claude-bp rules` names them): moves them beside their files, every line kept, and proves they still load. The founder runs it; it rewrites their instruction files."
disable-model-invocation: true
metadata:
  reviewed: "2026-11-23"
  recheck-after-days: 90
---

# Scope the instruction layer

The founder asked for this by running it: that ask is the consent decision 0114 requires
before their instruction files are touched. Nothing else in this plugin rewrites them.

What is being fixed. `CLAUDE.md` and every rule without `paths:` load into every turn of every
session. A rule that only matters beside some files (design beside screens, releasing beside the
store config) costs the same in a backend session as in a UI one, and every rule loaded at once
is followed less. A rule with `paths:` loads when a session reads, writes or edits a matching
file; this plugin's gate (decision 0113) asks for it before a new file or a command it governs.

## 0. Baseline

1. A card for this on the ledger, then a branch and a worktree, as for any change.
2. `BASE=$(git rev-parse HEAD)`, and keep it: every later check is against it.
3. `claude-bp rules`. Note the always-on total and the files largest first. Fix every PROBLEM
   it lists that is not about to be moved anyway, and say what it was.

## 1. Plan, before writing

For each always-on file, and for each section of the large ones (`##`/`###`), decide one of:

- **stays** — needed before any file is opened, in every session, or must survive compaction:
  prohibitions (secrets, production, pushes), how work is done (worktrees, PR flow, parallel
  sessions), how to search, how memory is used.
- **scoped** — about one area of the code: `paths:` naming that area's files, from where the
  rule's own text points and from `git log --format= --name-only` of the commits that changed
  the rule. Prefer a directory glob (`mobile/**`) to a list of files.
- **command** — about an act on no file (a release, a deploy, SQL on production): `paths:` for
  the files it does have, plus `commands:` with regular expressions of those commands
  (`make mobile-ota`, `eas (build|submit)`, `ssh .*prod`). Claude Code ignores `commands:`;
  this plugin asks for the rule before such a command.
- **skill** — a long procedure read only while doing it (deploy steps, a release checklist):
  `.claude/skills/<name>/SKILL.md`, with a description that says when.

A section that reads as meant for every session ("во всех чатах", "always", "never", "железно")
does not leave the always-on layer whole: keep a ONE-LINE invariant of it where it was, and move
the rest. Show the founder the plan as a table (section, from, to, why) and the invariant lines
before writing. In a session the founder told not to ask, write it and show the table after.

## 2. Write it

- **Move text, never rewrite it.** Cut and paste whole sections with their headings. The only new
  words are frontmatter, the invariant lines, and the routing table below.
- Frontmatter of a scoped file:
  ```
  ---
  paths:
    - "mobile/**"
  commands:
    - "make mobile-ota"
  ---
  ```
- A routing table in `CLAUDE.md`, one line per moved topic, so a plan made before any file is
  open still finds it: `- UI, screens, components → .claude/rules/design.md`.
- Fix every reference to a moved section (`→ «heading»`) to its new file.
- Duplicates: keep one copy, in the file that loads where it is needed; name the one removed.

## 3. Prove nothing was lost

```bash
claude-bp rules --since "$BASE"
```

It must end with exit 0. It lists:
- **lines in no instruction file now**: each is a deletion. Show every one to the founder; only
  after they agree, `--accept-dropped`. Lines are matched by their text, so a duplicate removed
  while its twin stays is NOT listed: name each such removal yourself.
- **now loaded only beside files**: every line that left the always-on layer. ⚑ marks one that
  reads as meant for every session: it needs an invariant line, or it goes back.
- **moved away from their heading**: move the heading with it.
- **problems**: a glob matching no tracked file, a scoped rule over 40,000 characters, a broken
  reference. All fixed.

## 4. Prove it loads

```bash
claude-bp rules --probe
```

A headless session per scoped rule reads one file its `paths:` match, and its transcript is
searched for the rule. Every row must say `loaded`. It costs one short model call per rule.

## 5. Finish

Commit with the before and after of `claude-bp rules` (always-on tokens) in the message, open the
pull request, close the card. Not this skill's to change, so name them to the founder instead:
- `~/.claude/rules/*.md`, loaded in every project: scope or move what is one project's.
- `MEMORY.md` past 200 lines or 25 KB (`claude-bp rules` says): its tail reaches no session.
- An MCP server configured twice (user and project scope), or skills and agents of one project
  installed for all of them: each costs its description in every session.
