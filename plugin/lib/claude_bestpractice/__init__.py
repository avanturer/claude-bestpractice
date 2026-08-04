"""claude-bestpractice: enforcement, memory and parallel-session coordination for Claude Code.

Design rule that governs every module here: nothing that matters is asked of the
model. Enforcement lives in the harness or in git; the model's context carries only
what no program can check.

Standard library only, deliberately. Hooks run on every tool call in every session; a
dependency tree is latency, a failure mode, and a supply-chain surface for a component
whose entire job is to be trustworthy.
"""

__version__ = "1.0.3"

MIN_PYTHON = (3, 9)
