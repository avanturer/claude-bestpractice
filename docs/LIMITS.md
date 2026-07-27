# What a false green still costs

This page exists because the alternative is a README that overstates, and this project's
entire thesis is that an unverified claim is worse than no claim.

Eight rounds of independent adversarial verification were run against the evidence gate
before v1.0. Each round was given the gate's source, told to reproduce by execution only,
and explicitly told not to trust `make check`, the doctor, or the test suite — all three
of which were green through every defect listed below. Every attack here was **run**, not
imagined.

## What eight rounds cost the attacker

The gate's job is not to make a false green impossible. It cannot be: the agent writes
your code, your tests, your test command, your build files and your runner configuration,
which is both sides of any comparison a gate can make. The job is to make a false green
expensive enough that it stops being the cheapest path to ending a turn.

| Round | What the gate trusted | What defeated it |
|---|---|---|
| 1 | a machine-readable artifact | a hand-written four-line JUnit file |
| 4 | the runner's exit code | `-` before a Makefile recipe |
| 5 | the words "N failed" in the output | not printing them |
| 6 | the count "N passed" | `@echo '2 passed in 0.03s'` |
| 7 | that count, with a floor | `@echo '9999 passed'` — one keystroke |
| 8 | the runner it drove itself | `addopts = --ignore=tests/test_x.py` |

Each row was fixed by the round that follows it. The last row is fixed in v1.0: the gate
invokes the runner binary itself with a report path outside the repository, blanks
`addopts` and `GOFLAGS`, pins the config file to one inside the repository, and compares
the reported count against test declarations it counts from your source tree.

## What still gets a green, and what it costs

These are known, reproduced, and unfixed. They are listed because a founder deciding how
much to trust this should decide on facts.

**A `conftest.py` that patches the bug away.** Three lines. Every test genuinely runs and
genuinely passes — nothing in the run is fake, only the process is. No count check can see
this, because the counts are all honest. Visible in a diff.

**A test that asserts nothing.** One edit. It counts as a test, it executes, it passes.
Visible in a diff.

**A runner shadowed on `PATH`, or a `.pth` in site-packages.** Needs no change inside the
repository at all, so there is **no diff to review**. This is the one that should worry
you most, and no amount of running the runner harder will surface it.

**A `testpaths` entry pointing at a decoy suite.** The decoys raise the structural count
too, so the floor is satisfied by them. Visible in a diff.

**Anything, when no runner is drivable.** For languages the gate cannot invoke directly,
it falls back to the command your project declares — and that command is a file the agent
writes. The count checks are what is left; they are weaker and the code says so.

## What this means in practice

The failure mode this actually prevents is the common one: an agent that is wrong rather
than adversarial. Across all eight rounds, every honest scenario — a real regression, a
narrowed run, an uncommitted file, a suite that skipped everything — was caught. Not one
required the founder to read a diff.

The failure mode it does not prevent is an agent that has decided to end the turn at any
cost and knows where to push. If you need a guarantee against that, this is not it, and
nothing that runs inside the same machine as the agent is.

## How to check any of this yourself

Every claim above is reproducible in a scratch repository in under a minute. The gate is
`plugin/bin/evidence-gate`; it reads a JSON event on stdin. `claude-bp doctor` proves the
gates fire by attempting known-bad actions rather than by reading configuration — but note
that it builds its own fixture repository, so it tells you the gates work, not that they
work in *your* repository.
