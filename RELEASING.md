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

## Cutting one

1. Bump the version in all six places (`tools/check_shipped.py` names them and refuses a
   change under `plugin/` without it).
2. Write the CHANGELOG entry: what broke, how it was found, what changed. Not a list of
   commits — the reader is someone deciding whether this affects them.
3. `make check` green.
4. Open the pull request, merge it. The release workflow cuts the tag and publishes the
   notes from the CHANGELOG. A session cannot push a tag; that is why this is a workflow.

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
