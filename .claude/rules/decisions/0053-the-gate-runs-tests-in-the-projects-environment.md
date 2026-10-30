---
title: The gate runs a project's tests in the project's own environment
paths: plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/evidence.py
date: 2026-10-30
---

## Decision
**The Stop gate starts pytest through `uv run --frozen python` where a `uv.lock` governs the
tests and uv is installed, through a virtual environment kept beside the configuration
(`.venv`, `venv`) where there is one, and with `python3` only where there is neither.** It looks
from the tests' project up to the top of the tree. The test command detected for a uv project
is the same `uv run` line; a `.venv` path is never baked into one, since every tree shares it.

## Why
> Гейт запускает pytest вне uv-окружения проекта: ошибки сбора ModuleNotFoundError (#282)

`backend/` is a uv project; the gate ran the `python3` PATH found first, every test file failed
to import `sqlalchemy`, and two failing of two were filed over a suite `uv run pytest` passed 33 of 33.

## Rejected
- **`uv run` without `--frozen`.** It rewrites `uv.lock`, and the gate would author the diff it judges.
- **The `.venv` before `uv run`.** A stale one fails a dependency the lockfile already has.
- **Activating `VIRTUAL_ENV` from the session.** The founder's shell is not the project.

## Cost accepted
The first run in a tree without `.venv` installs the locked packages inside the gate's budget.
