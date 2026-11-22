---
title: An edit is read by the project's own linter, on the lines it wrote
paths: plugin/lib/claude_bestpractice/editcheck.py, plugin/bin/pr-opened, plugin/hooks/hooks.json
date: 2026-11-22
---

## Decision
**After an `Edit`, `Write` or `MultiEdit`, the project's own linter is run on that file, and
what it finds on the lines the edit wrote reaches the session as the edit's
`additionalContext`.** Ruff where `ruff.toml`, `.ruff.toml` or `[tool.ruff]` governs the file,
ESLint where an `eslint.config.*` does, each only as installed in the project (a virtual
environment, `node_modules/.bin`) or, for ruff, on PATH. Run with `--no-fix`; never a formatter.
Silent where nothing is configured, installed or found, and past ten seconds. Heard by
`pr-opened`, on the PostToolUse entry it already has. `lint_on_edit off` stands it down.

## Why
> начни все реализовывать эффективно по времени понимая что это сделает только лучше

The founder asked for the per-edit check a research report on cheap Claude Code improvements
ranked first among this plugin's gaps (the Claude Code best-practices page gives the same hook
as its example). The Stop gate is the only check of an edit, and it runs the suite once the
turn ends: a typo in the first edit was found after the tenth, by a full run, as a refusal.

## Rejected
- **A formatter after each edit.** It rewrites the file under the session, and the next `Edit`
  is written against text that is no longer there.
- **The whole file's findings.** Every edit of a legacy file would carry its history, and a
  finding that always fires is read past (`discipline` holds the same line for stubs).
- **A thirteenth hook entry.** The budget is twelve; decision 0094 put a tool on this one.
- **The hooks module.** No process per edit, but no Claude Code this plugin is tested on shows
  what a module may add to an edit's result, and one it cannot run is a check that never fires.
- **`npx eslint`, `uv run ruff`.** Either may fetch or install inside a hook.
- **A type check (`tsc`, `pyright`) per edit.** Whole-program, seconds to minutes; the LSP
  plugins Claude Code recommends give the same diagnostics after an edit, from a warm server.

## Cost accepted
A Python process per edit, beside the one `pre-tool` starts, and the linter's own start: about
a second for ESLint. A finding an edit causes on a line it did not write (an import left unused
by a deleted call) is not said here; the suite or the commit review says it.
