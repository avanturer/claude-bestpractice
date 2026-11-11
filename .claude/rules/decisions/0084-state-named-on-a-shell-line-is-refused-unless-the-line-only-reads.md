---
title: Enforcement state named on a shell line is refused unless the line only reads it
paths: plugin/bin/pre-tool
date: 2026-11-11
---

## Decision
**A Bash line that names this plugin's enforcement state is refused unless every command that
names it is a plain read** (`cat`, `ls`, `grep`, `head`, `jq`, `git log`, `find` without an
action) with no redirection onto it. What the state is stays what `pre-tool` already guards:
Tier B, the config, the stage ratchet, the records no session may write. Compared without
regard to case, and resolved against a `cd` earlier on the line. A path kept in a variable is
judged where the variable is used; a command handed it in its environment names it.

## Why
> точно пойми что лучше в плагине нашем добавить или исправить, для такой разработки,
> мсштабируемости и устойчивости безщ багов

The gate listed the commands that write (`rm`, `mv`, `sed -i`, a redirect) and read their
targets. Sixteen ordinary spellings were on no list and went through: a glob or a brace in the
name, a name kept in a variable, `perl -pi`, `dd of=`, `find -delete`, `xargs rm`,
`git checkout <commit> --`, `git restore --source`, `sudo env truncate`, a path under `cd`,
`python3 -c` and a heredoc. The founder's machine has no managed settings and no sandbox, so
this parser is the only thing between a session refused four times and the file that would
end the refusals.

## Rejected
- **A longer list of writers.** Every list so far was one spelling short, found by a session.
- **Refusing every mention.** `cat config.json` is how a session learns why it was refused,
  and fifteen such reads are in the tests as left alone.
- **A sandbox.** The right layer, and the founder's to install; nothing here can rely on it.

## Cost accepted
A harmless command the reader list does not know, `awk` over the state among them, is refused,
and the refusal says the line is not a plain read. A program that builds the path at run
time, from parts the line does not spell, is still not seen.
