---
title: A line that merges is one merge, written so the gate can read it
paths: plugin/bin/pre-tool, plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/shellcmd.py, plugin/hooks/hooks.json
date: 2026-10-16
---

## Decision
**A shell line that merges runs one `gh pr merge`, as a command of its own.** It names its pull
request by number, by URL or by nothing, and joins whatever else it runs with `&&` only. The
rest is refused, with the merge written out to run alone: two merges on one line; one in a loop,
a condition or a group, behind `!`, `xargs`, `sudo`, `exec`, `eval`, `ssh` or a shell's `-c`; one
in a substitution bash expands, `"$(…)"` included; one through `gh api`; one named by branch or
by a variable; one sharing its line with `;`, `||`, `|` or `&`.

**`enable_pr_auto_merge` is a merge.** It waits for the word and spends it, and settles nothing,
as `gh pr merge --auto` does. `gh pr merge --help` and `--disable-auto` merge nothing.

## Why
> Two `gh pr merge` calls in one command (967 and 968) — refused; 968 alone a moment later — merged.

The gate judged the first merge on a line and nothing past it. With the word on #48 only, it
approved `gh pr merge 48 && gh pr merge 49` as judged one step ago: #49 merged on nobody's word,
with no prompt. Ten other spellings were no merge to it at all, measured on 2.4.0. A merge is
finished on its call's result (decision 0038), and behind `| tail` a failed merge read as done:
the word spent and the cards closed over a pull request still open. `--squash 2>&1` named #2.

## Rejected
- **Judging every merge on the line.** One call has one result: a `;` line whose first merge
  failed would finish both, the defect 0038 fixed, and each merge is judged in its own tree.
- **Refusing only the merge the word does not cover.** Its checks were never run either.
- **Reading whether it merged out of `gh`'s output.** Its wording is not an interface.

## Cost accepted
Merging several pull requests takes a call each, and a merge piped into `tail` is refused once.
`gh pr merge 5 >out` reads the `5` as a descriptor, as bash reads `5>out`.
