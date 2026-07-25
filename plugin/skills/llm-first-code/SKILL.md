---
name: llm-first-code
description: Write comments, docstrings and types for a model reader rather than a human one. Use when writing or reviewing any function, module or docstring, or when asked about comment style.
---

# LLM-first code

The person who owns this repository reads almost none of the code. You read all of it,
repeatedly, in fresh contexts with no memory of having written it. Optimise for that
reader.

## The rule that replaces most comments

**If you are about to write a comment describing what a value is, write a type
instead.** A type is checked; a comment is a claim nobody verifies. 94% of LLM compile
errors are type errors, and type constraining halves them.

## Workflow

1. **Types first.** Full annotations. No `Any`, no bare generics, no untyped
   `*args`/`**kwargs`, no unlabelled suppressions. A suppression carries its error
   code or it does not ship.
2. **Write a docstring only if it carries something non-derivable.** Preconditions,
   invariants, why-not-what, the failure mode, the thing that will surprise the next
   reader. If the signature already says it, say nothing.
3. **Never restate the signature.** `Args:`, `Returns:`, `Parameters:`, `:param:`,
   `@param` are banned outright. They duplicate what types already state, they cost
   context on every read, and they rot independently of the code.
4. **Comment the decision, not the mechanism.** `# sorted by mtime because the caller
   pages from newest` is worth its tokens. `# sort the list` is not.
5. **When you change a signature, change its docstring in the same edit.** A gate
   hashes `(param names, param types, return type)` and fails the commit when the
   signature moved and the docstring did not.
6. **Delete before correcting.** Remediation order for a wrong comment is: delete it,
   then consider rewriting. A deleted comment costs nothing; a stale one actively
   misleads.

## Why staleness is the whole point

Stale context is measurably **worse than none**. Retrieval carrying only outdated
material induced calls to dead APIs on 15 of 17 samples; retrieval carrying nothing
produced 0 of 17. Without context a model fails visibly. With stale context it binds
confidently to something that no longer exists.

And you cannot detect this yourself. Every frontier model tested — including current
ones — loses 21 to 43 percentage points of detection accuracy when an implementation
changed while its docstring stayed plausible. That is exactly the drift a repository
with several parallel sessions produces continuously.

So staleness is caught mechanically, never by judgement. Your part is to not create it.

## Banned outright

- Signature restatement in any docstring convention.
- `__init__` docstrings.
- Narration comments: `# Step 1:`, `# Loop over the items`, `# Return the result`.
- Commented-out code. Git already remembers it.
- `TODO` / `FIXME` without an owner and a condition for removal.
- Emoji, banners, decorative separators.
- Any comment that would be identical after a rewrite of the function.

## Common issues

- **A docstring that is a summary of the body.** If it can be regenerated from the
  code, it is not worth a read. Delete it.
- **Module headers listing the directory contents.** Derivable, and the harness's own
  doctor trims exactly this class of content.
- **A comment explaining a workaround with no link to why.** Name the constraint —
  the upstream bug, the API limit, the ordering requirement — or the next reader
  removes the workaround.
- **Type annotations that lie.** `-> dict` on a function returning three shapes is
  worse than no annotation: it is checked, believed, and wrong.

For the full pattern catalogue with examples, see `references/PATTERNS.md`.
