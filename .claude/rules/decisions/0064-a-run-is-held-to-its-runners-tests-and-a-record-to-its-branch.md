---
title: A run is held to the tests its runner runs, and a red record to its own branch
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/testcount.py, plugin/lib/claude_bestpractice/delivery.py
date: 2026-11-01
---

## Decision
**The gate's own run is held to the tests its runner runs**: pytest to the tree's `.py` test
files, `go` to its `.go` ones (`witness.SUFFIX`). A pass that reaches them is the run that went
red passing, and clears its record. Where the tree also declares tests in other languages, which
nothing the gate drove ran, the finish stays UNVERIFIED, writes no green, and names them.

**A red record keeps the bar of its own run**: counted in its runner's language (`declared_in`),
never carried from a record counted in other languages or about another branch.

**A red record another branch left in a tree stands in no way of the branch checked out there**:
not its pull request, its merge, nor `claude-bp verify`'s status, which 0063 tied to any record.
It stays that branch's, cleared with that branch checked out.

## Why
> a pass of the gate's own `pytest` clears the red record for that command. Vitest tests are not
> counted as tests pytest should have run.

27 pytest tests beside 1,338 Vitest ones: the tree declared 1,365, so every pass of pytest read
as a run narrowed to a fiftieth, and `claude-bp verify` on the branch that went red cleared
nothing. On the branch the tree had moved to, the merge was refused over that record (#290).

## Rejected
- **The other languages' tests in the run's count.** No pass of its runner can reach them.
- **A pytest pass as a witnessed green of the whole tree.** The Vitest tests ran nowhere, and a
  push would skip the run that has them.
- **The project's command for the rest at every finish.** The recipe 0063 keeps out of a finish.
- **Dropping another branch's record.** It is a failure of that branch's code, unrun since.

## Cost accepted
In a tree of two languages a finish the gate's runner alone judged stays unverified, as before,
for the founder's `+merge` to decide (0010). Another branch's record stays on this tree's board
until that branch passes here.
