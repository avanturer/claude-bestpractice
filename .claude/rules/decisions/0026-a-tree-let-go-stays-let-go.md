---
title: A tree the Stop gate let go stays let go until something in it changes
paths: plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/evidence.py
date: 2026-10-01
---

## Decision
**A finish let go UNVERIFIED is remembered with the tree it was about.** The tree is named by the
commit its diff is measured from and the content of every file in that diff, under this version
of the gate and the config it read. While a later Stop sees that same tree, the suite is not run
again, and what the gate would say besides the suite is refused only if it is new. The finish
is UNVERIFIED again, never a pass.

Bounded the way decision 0013 bounds a remembered failure: after an hour the tree is judged
from the start, and any change to the tree, the gate or the config ends it at once.

## Why
> Если дерево не менялось, повторный прогон не запускает «[1/4]» заново.

The ceiling let a tree go after four blocks, and the next message began four more over the same
tree, each a full run of 5,887 tests, with nothing in the tree changed since (#255). Every one
of them could only end where the first had: unverified, on record, the founder told.

## Rejected
- **Remembering a pass instead.** Nothing here passed, and 0013 forbids that in any case.
- **Keying on `HEAD^{tree}`, as 0013 does.** A session in the middle of its work has
  uncommitted changes, and a dirty tree hashes to nothing there, so the memory would never hold
  where it is needed. The diff's floor and its files' content are what the suite saw.
- **Raising the ceiling, or resetting it less often.** Either still runs the suite again over
  content it has already judged.

## Cost accepted
A suite that would pass once something outside the tree is fixed (a database, a package) is not
run again for up to an hour on an unchanged tree. Editing anything in the tree ends that.
