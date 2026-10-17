---
title: A line that merges is one merge, written so the gate can read it
paths: plugin/bin/pre-tool, plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/shellcmd.py, plugin/hooks/hooks.json
date: 2026-10-16
---

## Decision
**A shell line that merges runs one `gh pr merge`, as a command of its own, on a line that fails
whenever it does.** It names its pull request by number, by URL or by nothing. The rest is
refused, with the merge written out to run alone: two merges on one line; one in a loop, a
condition or a group, behind `!`, `xargs`, `sudo`, `exec`, `eval`, `ssh` or a shell's `-c`, or
in a document or here-string a shell reads; one in a substitution bash expands, `"$(…)"` and an
unquoted heredoc included; one through `gh api`; one named by branch or by a variable; one piped
on, followed by `;`, `||` or `&`, or behind `||`.

**Its words are read as bash hands them to `gh`.** A number written against `<` or `>` is that
redirection's descriptor and `5 >out` names #5, a quoted `;` or `>` is text, a flag's value is no
flag, and a substitution runs only its own text.

**`enable_pr_auto_merge` is a merge.** It waits for the word and spends it, and settles nothing,
as `gh pr merge --auto` does. `gh pr merge --help` and `--disable-auto` merge nothing.

## Why
> Two `gh pr merge` calls in one command (967 and 968) — refused; 968 alone a moment later — merged.

With the word on #48 only, 2.4.0 approved `gh pr merge 48 && gh pr merge 49`, and ten other
spellings were no merge to it. A merge is finished on its call's result (decision 0038), and
behind `| tail` a failed merge read as done. This release's review found its own reading wrong
in bash's terms: `5 >log` judged as the current branch's merge, `10>log` as #10.

## Rejected
- **Judging every merge on the line.** One call has one result: a `;` line whose first merge
  failed would finish both, the defect 0038 fixed, and each merge is judged in its own tree.
- **Refusing only the merge the word does not cover.** Its checks were never run either.
- **Reading whether it merged out of `gh`'s output.** Its wording is not an interface.
- **`&&` as the only joint.** `cd <tree>; gh pr merge` fails with the merge, and was refused.

## Cost accepted
Merging several pull requests takes a call each, and a merge piped into `tail` is refused once.
A `$(case …)`, or a document `cat` prints into a shell's `-c`, is read to the end of the line.
