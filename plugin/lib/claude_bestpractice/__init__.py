"""claude-bestpractice: enforcement, memory and parallel-session coordination for Claude Code.

Design rule that governs every module here: nothing that matters is asked of the
model. Enforcement lives in the harness or in git; the model's context carries only
what no program can check.

Standard library only, deliberately. Hooks run on every tool call in every session; a
dependency tree is latency, a failure mode, and a supply-chain surface for a component
whose entire job is to be trustworthy.
"""

__version__ = "2.2.0"

# What Claude Code calls this plugin, and the marketplace that lists it: `plugin.json` and
# `marketplace.json` say the same, and `PLUGIN@MARKETPLACE` is the id `enabledPlugins` and
# `claude plugin install` use. The plugin is not called `claude-bestpractice` because Claude
# Code 2.1.289 reserves every plugin name that starts `claude-` (decision 0029).
PLUGIN = "bestpractice"
MARKETPLACE = "claude-bestpractice"

MIN_PYTHON = (3, 9)
