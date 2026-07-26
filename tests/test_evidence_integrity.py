"""Three ways a green finish was available without the tests passing.

Each was found by execution, and each defeated the gate's central claim — that completion
is accepted on evidence and never on assertion — while the doctor reported every check
passing and the suite reported OK.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import RepoCase, git


class TestTheRunnersOwnWordsOutrankItsExitCode(RepoCase):
    """A Makefile recipe prefixed with `-`, a `|| true`, a wrapper that eats the status.

    All three are ordinary, and all three handed the gate exit 0 over the literal text
    "1 failed". The gate called it green and cleared the red-suite ledger.
    """

    def failing_suite_behind_a_swallowed_status(self) -> None:
        self.write("tests/test_x.py", "def test_broken():\n    assert False\n")
        self.write("Makefile", "test:\n\t-python3 -m pytest -q\n")
        self.commit("suite")
        self.write("src.py", "x = 1\n")

    def test_it_is_refused(self):
        self.failing_suite_behind_a_swallowed_status()
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo)},
        )
        self.assertEqual(proc.returncode, 2, "a suite reporting failures finished green")
        self.assertIn("FAILING and then exited 0", proc.stderr)

    def test_the_refusal_names_the_cause_rather_than_the_symptom(self):
        """"Tests failed" would send the agent to fix passing tests. The status is a lie."""
        self.failing_suite_behind_a_swallowed_status()
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo)},
        )
        self.assertIn("swallowing the exit status", proc.stderr)

    def test_a_genuinely_passing_suite_is_still_accepted(self):
        """The check is worthless if it refuses the honest case too."""
        from founder_os import evidence

        self.assertEqual(evidence._failures_from_output("4 passed in 0.10s"), 0)
        self.assertEqual(evidence._failures_from_output("Ran 4 tests in 0.1s\n\nOK\n"), 0)

    def test_it_reads_unittest_as_well_as_pytest(self):
        from founder_os import evidence

        self.assertEqual(evidence._failures_from_output("FAILED (failures=2, errors=1)"), 3)
        self.assertEqual(evidence._failures_from_output("2 failed, 3 passed in 1.0s"), 2)


class TestByproductDirectoriesDoNotHideSource(RepoCase):
    """`coverage/` is a report directory in most repositories and a package in some.

    The component match could not tell them apart, so every source file under any
    directory named coverage, htmlcov, .tox or .gradle — at any depth — was invisible to
    the gate, and a red suite in that code was never run at all.
    """

    def test_source_under_a_byproduct_directory_is_visible(self):
        from founder_os import evidence

        for path in (
            "src/coverage/rules.py",
            "app/htmlcov/view.py",
            "svc/coverage/billing.py",
            "pkg/.tox/helper.py",
            "web/coverage/report.ts",
        ):
            self.assertTrue(
                evidence.material_changes([path], exempt=()),
                f"{path} is invisible to the gate",
            )

    def test_actual_byproducts_are_still_hidden(self):
        """Otherwise the gate reports its own test run as the agent's scope drift."""
        from founder_os import evidence

        for path in (
            "__pycache__/mod.cpython-311.pyc",
            "htmlcov/index.html",
            "coverage/lcov.info",
            ".pytest_cache/v/cache/lastfailed",
            ".gradle/7.6/checksums.lock",
        ):
            self.assertFalse(
                evidence.material_changes([path], exempt=()),
                f"{path} is reported as a material change",
            )


class TestTheGatedPartyCannotAmendTheRules(RepoCase):
    """`config.json` names the command the Stop gate runs, and lives where the agent writes.

    One Write call — `{"test_command": ["true"]}` — bought a green finish and deleted the
    red-suite ledger recording the real failure. Nothing downstream could tell afterwards.
    """

    def write_event(self, relpath: str) -> dict:
        return {
            "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / relpath), "content": "{}"},
        }

    def decision(self, proc) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def test_the_config_is_refused(self):
        proc = self.run_hook("pre-tool", self.write_event(".claude/founder-os/config.json"))
        self.assertEqual(self.decision(proc), "deny")

    def test_the_stage_ratchet_is_refused(self):
        proc = self.run_hook(
            "pre-tool", self.write_event(".claude/founder-os/stage/reached-revenue.json")
        )
        self.assertEqual(self.decision(proc), "deny")

    def test_the_slop_budget_is_refused(self):
        proc = self.run_hook("pre-tool", self.write_event(".claude/founder-os/slop-budget.json"))
        self.assertEqual(self.decision(proc), "deny")

    def test_the_shell_route_is_refused_too(self):
        """Every rule in this gate has been routed around with a heredoc at least once."""
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
                "tool_name": "Bash",
                "tool_input": {"command": "echo '{}' > .claude/founder-os/config.json"},
            },
        )
        self.assertEqual(self.decision(proc), "deny")

    def test_the_agents_own_ledgers_are_still_writable(self):
        """The agent is MEANT to write tasks, decisions and dead ends. Over-refusing here
        would break the memory layer to protect the config."""
        for relpath in (
            ".claude/founder-os/plan/next/0001-task.md",
            ".claude/founder-os/decisions/0001-x.md",
            ".claude/founder-os/attempts/0001-y.md",
        ):
            proc = self.run_hook("pre-tool", self.write_event(relpath))
            self.assertIsNone(self.decision(proc), f"{relpath} was refused")

    def test_ordinary_source_is_untouched(self):
        proc = self.run_hook("pre-tool", self.write_event("src/app.py"))
        self.assertIsNone(self.decision(proc))


if __name__ == "__main__":
    unittest.main()
