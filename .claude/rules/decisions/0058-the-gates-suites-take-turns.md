---
title: The suites the gates of one clone run take turns
paths: plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/evidence.py
date: 2026-10-30
---

## Decision
**The suites the Stop gates of one clone start run at most as many at once as fit in half the
CPUs at `witness_cpus` each**, one where `witness_cpus` is every CPU. A run waits for a place,
held in the git common dir as a lock file each, and pinned to CPUs of its own by the place it
holds. The wait counts against the run's time, and the founder writing ends it as it ends a run.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

Six to twelve sessions worked on one 24-thread machine, and each Stop started its suites the
moment its turn ended. The load reached 30 to 44, suites that pass alone failed under it and were
filed red, and the machine went down twice in a day. `witness_cpus` bounded each run and not how many
ran: twelve runs of four CPUs are forty-eight.

## Rejected
- **A smaller `witness_cpus`.** Every run gets slower and twelve still start at once.
- **A machine-wide queue in the home directory.** State keyed outside the clone is what
  decision 0001 refused; one clone is where the sessions that collide work.
- **Skipping the run when the machine is busy.** A finish then goes unverified for load.

## Cost accepted
When several sessions finish at once, the last one's verdict comes after the runs before it.
The Stop gate runs after the turn (decision 0046), so the founder is not held by the wait.
