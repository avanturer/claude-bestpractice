---
title: A suite the gate starts gets its share of the machine, not the machine
paths: plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/config.py
date: 2026-10-15
---

## Decision
**Every suite the Stop gate starts runs on at most `witness_cpus` CPUs, four by default**, by
narrowing the CPU affinity mask the process inherits. `0` is every CPU. Which CPUs turns on the
gate's process id, so several sessions' gates spread over the machine.

## Why
> Собственные прогоны гейта не забирают все ядра и всю память общей машины.

jest sizes its pool as `os.availableParallelism()` less one: 23 workers of up to 0.9 GB on a
24-thread WSL machine, for a session that was running nothing, beside six to twelve others (#267).
`availableParallelism` reads the affinity mask (jest 29.7 and 30.5 measured, Node 22), and so does
Go's GOMAXPROCS, so one mask bounds every such runner without knowing its flags.

## Rejected
- **`--maxWorkers=4` on the command.** Only jest knows it; `npm test` may run anything.
- **Checking free memory before a run.** It cannot say what the run will take, and refusing to
  run is an unverified finish nobody earned.
- **No default.** The machine this was reported on is the one the plugin is for.

## Cost accepted
A suite that parallelises runs slower under the gate than by hand, and may outrun the Stop hook
sooner. Where no mask can be set, macOS and Windows, the run starts as before.
