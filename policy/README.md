# Policy layer

The rules that must hold cannot live in the plugin. A plugin's own `settings.json` honours only
`agent` and `subagentStatusLine`, so a plugin can ship hooks, skills, agents, output styles and MCP
declarations — and nothing policy-shaped. Every deny rule, sandbox setting and lock lives here.

## What this is and is not

This is a machine owner hardening **their own** agent sessions with documented settings. The threat
model is *the agent must not do this by accident or by confabulation* — not *the owner must be
prevented from administering their computer*. A human with `sudo` can always undo all of it, and
should be able to.

The vendor documents self-lockout as a supported use:

> *"A user can set it in their own settings to lock themselves out of bypass mode."*

## Where it goes

| Platform | Path |
|---|---|
| Linux / WSL | `/etc/claude-code/managed-settings.json` (+ `managed-settings.d/*.json`) |
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` |

Root-owned, mode `0444`. Managed settings sit above command-line arguments in precedence, so a later
override flag cannot climb out.

## Before you use the example

**Verify every key against the binary you actually have.**

```sh
claude --version
which claude          # resolve the real binary; a stale package copy elsewhere on
                      # disk produced three wrong conclusions during research
```

A misspelled or removed key **silently does nothing**. That is the worst possible failure here: it
looks configured and enforces nothing. There is no stability contract — 353 releases, roughly one a
day, no semver guarantee, no deprecation policy.

So the example ships commented and inert. Enable keys one at a time, and after each one run the
doctor, which **proves** the rule fires by attempting a known-bad action in a scratch directory and
asserting the denial. Config-readback cannot detect a semantics change; two documented behaviours
have already shifted under this design.

## The one setting that is not optional

If the OS sandbox is enabled but its backend is missing, Claude Code prints a warning and **runs with
zero enforcement**. Observed in testing: a command escaped the project and wrote outside it.

Ship `failIfUnavailable: true`, or every guarantee in this repository is fiction.

## What the sandbox buys that permission rules cannot

Permission rules parse command strings; they are not a security boundary. The documentation says so:

> *"Read and Edit deny rules … don't apply to arbitrary subprocesses that read or write files
> indirectly, like a Python or Node script that opens files itself."*

Confirmed here: under a deny rule on a build directory, a direct append was denied while a
shell-wrapped write and a three-line interpreter write both landed bytes on disk. Only the
kernel-level sandbox stops those, because it constrains the child process regardless of how it was
spawned.

The sandbox also auto-denies writes to `settings.json` at every scope and to the managed directory,
so a sandboxed command cannot rewrite its own policy.

**Not available on native Windows.** Say so rather than shipping a false guarantee.

## What still does not hold

1. **Bare mode** drops managed hooks and plugin hooks alike. Nothing in-product covers it.
2. **MCP servers run outside the sandbox** — a remote server writing through an API bypasses every
   local control. The MCP allowlist is mandatory, not optional.
3. **Test semantics and taste** are not machine-checkable at all.

For anything that must hold even with the plugin uninstalled, use the repo layer: real git hooks
under a root-owned hooks path, CI, and branch protection with required status checks the agent has no
credentials to alter.

> The plugin binds the agent. The repo binds everyone.
