# Releasing

Written down because the alternative is a ritual that lives in whoever cut the last one.

## Versioning

Semver, and the distinction that was being ignored: **a new capability is a minor bump.**
Nineteen releases went out as `1.0.x` including ones that added whole behaviours — pull
requests as obligations, standing instructions, defect reporting. Anyone reading the
version to decide whether an upgrade was safe was reading a number that could not tell
them.

| change | bump | example |
|---|---|---|
| a gate refuses something it used to allow, a config key changes meaning, state on disk stops being readable by the old code | **major** | none yet |
| a new gate, a new command, a new config key, a behaviour that did not exist | **minor** | pull-request obligations, the `standing` marker |
| a defect fixed, a message corrected, a test added | **patch** | every `1.0.x` that fixed a report |

The version is the only thing `claude plugin update` compares. It is the whole contract.

## While working

`make test-fast` runs the same tests across shards — 77s against 173s on four cores. Use it
to iterate. It is not the gate and cannot be: one process is what lets this suite catch
state leaking between tests, and sharding is what hides it. `make check` stays serial.

Reach for a single module before either — `python3 -m pytest tests/test_gitpolicy.py` is
twelve seconds, and most of a fix is answered by one file.

## Proving a new test proves something

A test written alongside the code it covers passes for two different reasons, and only one
of them is worth anything. So break the behaviour on purpose and confirm the test goes red.

Two ways that exercise has silently produced nothing, both found here rather than
theorised:

- **The mutation never applied.** A replacement string that does not match the source
  leaves the file untouched, the run is green, and the green means nothing. Assert the
  anchor appears exactly once and that the text actually changed, before running anything.
- **The run measured the previous mutation.** CPython validates a `.pyc` against
  `(source mtime seconds, source size)`, so two mutations written inside the same second
  can collide there and the interpreter reuses stale bytecode. Three mutations were
  reported as survivors this way, one of which was provably caught when run by hand. Sweep
  `__pycache__` and run with `-B` and `PYTHONDONTWRITEBYTECODE=1`.

A surviving mutation is a defect in the test, not a nuisance. Both survivors that remained
after fixing the harness above were real: one asserted a type check using a value the check
never reached, the other asserted tolerance that a lower layer was already providing.

## Cutting one

1. Bump the version in all six places (`tools/check_shipped.py` names them and refuses a
   change under `plugin/` without it).
2. Write the CHANGELOG entry: what broke, how it was found, what changed. Not a list of
   commits — the reader is someone deciding whether this affects them.
3. `make check` green.
4. **If the branch had to merge the trunk, run `make check` again.** A green check before
   a conflict resolution says nothing about the tree after it. Resolving to "our" side is
   right only when our side is a superset — and it is not, whenever this branch *replaced*
   something the trunk also changed, because the merge then keeps both copies.

   That shipped a broken `main`: two definitions of the same function, Python taking the
   older one, `NameError` in the path the Stop gate runs suites with. The release workflow
   caught it on the slop gate and cut no tag, so nothing reached the marketplace — but CI
   was the only place the check still ran, because `--no-verify` had been bypassing the
   pre-push hook.

   The rule is not "resolve carefully". It is: **the check that counts is the one after
   the last change to the tree.**
5. Open the pull request, merge it. The release workflow cuts the tag and publishes the
   notes from the CHANGELOG. A session cannot push a tag; that is why this is a workflow.
6. Re-arm this repository under what just shipped:

   ```
   claude plugin marketplace update claude-bestpractice
   claude plugin update claude-bestpractice@claude-bestpractice --scope project
   ```

   `--scope project` is not optional: the enablement is committed in the repository, so
   the install is project-scoped, and `update` defaults to `user` and fails with
   *"Plugin is not installed at scope user"*. Written down because this step shipped
   without it and did not work the first time it was run.

   This repository develops the plugin and runs under it, at the version on the default
   branch rather than the working tree — so a session editing a gate is judged by the gate
   its users have, and a half-written one cannot lock the repository writing it. The
   trade is that the installed copy is one release behind the tree until this step runs,
   which is why it is a step and not a hope.

   On a machine that has never had it, `claude plugin install
   claude-bestpractice@claude-bestpractice` once; the marketplace and the enablement are
   already committed in `.claude/settings.json`.

## When a release turns out to be bad

A released version cannot be withdrawn. The tag is permanent and `claude plugin` will keep
serving it, so an old release always looks fine from the outside — which is exactly how
someone stays on a version that cannot push.

So the plugin says it itself. Add the version to `upgrade.KNOWN_BAD` with one line of why,
and every session running it is told, on the board and in `claude-bp status`. That reaches
the person on the bad version, which release notes never do.

A version belongs there when it **broke something that worked** — not when a later release
improved on it. Everything is superseded eventually; that is not a defect.

## Branches

One branch, squash-merged, and that combination is what produced the merge dance in this
repository's history: squashing rewrites the commit, so the branch tip stops being an
ancestor of `main`, and the next push is rejected as non-fast-forward.

The fix is a repository setting, not a technique: **Settings → General → "Automatically
delete head branches"**. With it on, the branch disappears when the pull request merges,
the next push recreates it from `main`, and every push is a plain fast-forward.

Until then, the sequence that works without a force-push:

```
git fetch origin main
git checkout -B <branch> origin/main        # start from what shipped
# ... work, commit ...
git merge -s ours origin/<branch>           # absorb the stale pre-squash tip
git push -u origin <branch>
```

`-s ours` is safe **only** after checking that the remote tip carries nothing `main` does
not: `git diff origin/<branch> origin/main` must be empty. If it is not, something real is
on that branch and merging it away would delete it.
