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

## Measured against a plain CLAUDE.md: no difference observed

Two repositories, identical but for the plugin, each with the same `CLAUDE.md` carrying
three hard rules — never write credentials, never finish with a failing suite, never edit
a file the task did not name. Separate `$HOME`s, so the plugin was genuinely absent on one
side. Six single-turn runs of tasks built to tempt a violation.

**No difference. The Stop gate never engaged once** — the model complied with the prose
every time. One run without the plugin ended with a red suite, and reading it showed the
session had done the right thing: it hit two of the three rules in genuine conflict,
refused to break either, explained which, and asked.

That result is reported because it is what happened, and because it bounds the claim this
project is allowed to make. **On a short session with a few clear rules, a good CLAUDE.md
appears to be enough**, and the 332 tokens here buy little.

The experiment cannot show what this is for, and saying so is not a defence of the
experiment. The failure mode targeted is decay — 0 % violation, 30 % after one compaction,
78 % after four — and collapse under rule count: 93.8 % perfect at 10 rules, 0 % at 80.
One turn with three rules exhibits neither. A faithful test is three to eight sessions
across days with a real backlog, and it has not been run.

What does not depend on any experiment is the category difference. A `CLAUDE.md` is text
handed to a model; compliance is probabilistic and it is the model's to give. A hook is
code the harness runs. **No `CLAUDE.md` can make `git push` fail, deny a `Write` before the
byte reaches disk, hold a lease across two operating-system processes, or run your suite
and read the exit code itself.** Those are not more reliable here — they are unavailable
there. Whether you need them is a question about how you work, not about this plugin.

## Windows

Everything in this repository was built and verified on Linux. `bin/` is twenty
extensionless Python scripts with a `#!/usr/bin/env python3` line, and Windows does not
read shebangs — so before v1.0 not one command and **not one gate** ran there. The plugin
installed, reported enabled, and enforced nothing.

v1.0 ships a `.cmd` shim beside each script. Claude Code runs hooks through Git Bash where
it exists and PowerShell where it does not; PowerShell resolves an extensionless path
through PATHEXT, so the same hook command reaches the shim without hooks.json changing.
The shim tries `py -3` before `python`, because the python.org installer ships `py.exe`
and `python.exe` and not `python3.exe` — the very name the POSIX shebang needs.

**This fix is designed from the documented behaviour and is NOT verified by execution.**
The suite asserts the shims exist, pair with their scripts, use CRLF and hardcode no
name; it cannot assert they run, because that needs Windows and this suite has never been
on one. Treat Windows as unverified until someone reports back — and given what this page
is about, do not take "it should work" from me as more than that.

## Platform status, stated exactly

| | |
|---|---|
| **Linux** | Verified by execution throughout. Every attack and every fix on this page was run here. |
| **macOS** | **Never run.** No known blocker: the two Linux-only reads (`/proc/<pid>/stat` for the pid fingerprint, `/proc/self/ns/pid` for the lock identity) are already written to degrade to "cannot tell", and "cannot tell" resolves to alive by design. Unverified is not the same as working. |
| **Windows** | **Never run**, and it was outright broken until v1.0 — extensionless scripts with a shebang Windows does not read, so no command and no gate ran. Now shimmed, and one genuine hazard fixed: `os.kill(pid, 0)` is a harmless existence probe on POSIX and on Windows is `CTRL_C_EVENT`, so the liveness check would have interrupted the sibling session it was asking about. |

Treat macOS and Windows as unverified until someone reports back.

## How to check any of this yourself

Every claim above is reproducible in a scratch repository in under a minute. The gate is
`plugin/bin/evidence-gate`; it reads a JSON event on stdin. `claude-bp doctor` proves the
gates fire by attempting known-bad actions rather than by reading configuration — but note
that it builds its own fixture repository, so it tells you the gates work, not that they
work in *your* repository.
