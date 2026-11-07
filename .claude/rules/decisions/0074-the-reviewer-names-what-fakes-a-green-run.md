---
title: The commit reviewer names the moves that make a red suite read green
paths: plugin/lib/claude_bestpractice/reviewrules.py, plugin/bin/review-commit
date: 2026-11-06
---

## Decision
**The commit reviewer reports six more classes, each a shape no honest test needs**: a test
command that cannot fail (`|| true` after a runner, `--passWithNoTests`, `-DskipTests`,
`ignoreFailures`, `addopts` that deselects or ignores tests), `__eq__` answering True to
anything, a test that exits the process, a test hook that rewrites a result, an assertion that
cannot fail, and a test that returns before it asserts. Focused and skipped tests are read in
more runners' spellings (`fit`, `fdescribe`, `xfail`, `@unittest.skip`, `#[ignore]`,
`@Disabled`), a bare `fit(` or `xdescribe(` only before the description a test call opens
with. The four that are only tampering inside a test read test files only.

As every finding here: reported, never blocked, and the founder's to merge past once shown.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

`docs/LIMITS.md` lists what still gets a green past the Stop gate, a conftest that patches the
bug away first; `DESIGN.md` §5.3 asked for a skip, a deleted test or an assertion-free test to
be caught at commit, and the reviewer caught a skip. isitdone publishes its detectors and
catches 102 of 103 tampering cases with none of 89 honest changes flagged; arXiv 2610.12822
(October 2026) found exit-code tricks, always-equal objects and conftest hooks passed between
agents through code and memory, and reduced by review.

## Rejected
- **Weakened assertions and widened tolerances.** Telling `toEqual` that replaced `toStrictEqual`
  from one written fresh needs the diff's pairs, not a pattern; a reviewer that guesses is ignored.
- **Blocking the commit.** Decision 0002's gate is the suite; this is what a person reads.
- **`.only(` on any receiver.** It flagged Django's `QuerySet.only()`; now `it`, `test`, `describe`.
- **`fit(` on its own.** Run over fuddy it named four honest lines and no real one: the landing's
  `function fit()` and the meal planner's `fit(day.kcal, eaten?.kcal)`.

## Cost accepted
A smoke test ending in `assert True` is named. Deleting that line answers it. A focused test
whose description starts on the next line is not.
