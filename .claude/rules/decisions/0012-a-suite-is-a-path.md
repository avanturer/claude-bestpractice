---
title: A suite is a path, and a turn is judged by the suites its diff reaches
paths: plugin/lib/claude_bestpractice/suites.py
date: 2026-09-12
---

## Decision
A test suite is a command plus the directory it answers for. `test_commands` in
`config.json` declares them, subprojects carrying a runner of their own are detected, and
the Stop gate runs only the suites the turn's material diff reaches. `test_command` is the
suite for everything no scoped suite claims, so a single-project repository is unchanged.

A declared suite and a detected one are trusted differently. Declared is the founder's
promise about how those files are tested, and a wrapper whose runner is missing refuses the
finish. Detected is this gate's guess, and a guess that cannot run is dropped in favour of
the repository-wide command — inference may make a finish easier to earn, never harder.

Everything a verdict is computed from follows the suite: the declared-count floor, the
artifact search, the clean-checkout re-run, and which suite the red ledger names.

## Why
A mobile-only pull request was refused four times over a backend pytest failure caused by
the contents of a local database. Nothing in the diff reached the backend, the jest suite
that did cover the change was invisible because the gate knew one command, and the gate had
already told the session not to push changes to make the check pass (#206).

A suite that a change cannot have broken is not evidence about that change. Demanding it
anyway spends the hook's whole budget to answer a question nobody asked, and teaches the
one thing this plugin cannot afford to teach: that the gate is noise.

## Rejected
- **Failing-test paths against the diff.** Parsing which tests failed and comparing their
  files to the diff reads the runner's output, which is the party being gated. Selection by
  path is decided before anything runs.
- **Standing down whenever the suite looks unrelated.** A gate that excuses itself on a
  guess excuses itself in every unusual repository.
- **One suite per subproject, always run.** Five suites do not fit in one Stop hook. More
  than three in a plan collapses to one wide run instead.
- **Asking the founder to declare every suite.** Detection is this plugin's default posture
  (`config.py`); a repository that needs no config file must still get correct behaviour.

## Cost accepted
Detection probes up to eight directories two levels down, once per turn. A scoped green
proves less than a wide one, so it is stamped as scoped and cannot authorise the push-time
skip.
