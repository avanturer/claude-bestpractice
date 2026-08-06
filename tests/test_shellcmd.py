"""A gate decides on what a line RUNS, not on what it contains.

Issue #76: every command whose text held the merge invocation was refused as a merge —
`echo` of it, `grep` for it in documentation, a script carrying it as a JSON payload. So
reporting on the gate, quoting it, or searching for it all became impossible, and the tool
for investigating the gate was blocked by the gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import pullrequest, shellcmd


class TestReadingIsNotDoing(unittest.TestCase):
    def merges(self, line: str) -> bool:
        return pullrequest.merge_target("Bash", line, {}) is not None

    def test_the_real_invocation_is_still_a_merge(self):
        self.assertTrue(self.merges("gh pr merge 461 --admin --squash"))
        self.assertEqual(461, pullrequest.merge_target("Bash", "gh pr merge 461", {}))

    def test_talking_about_it_is_not(self):
        for line in (
            "echo 'the report said: gh pr merge 461'",
            "grep -rn 'gh pr merge' docs/",
            "cat CHANGELOG.md | grep -c 'gh pr merge'",
            "printf '%s\\n' 'gh pr merge'",
        ):
            self.assertFalse(self.merges(line), line)

    def test_it_survives_the_ways_a_command_is_really_written(self):
        for line in (
            "cd /tmp && gh pr merge",
            "timeout 30 gh pr merge 5",
            "nice -n 5 gh pr merge",
            "GH_TOKEN=x gh pr merge 7",
            "/usr/bin/gh pr merge",
        ):
            self.assertTrue(self.merges(line), line)

    def test_looking_a_program_up_is_not_running_it(self):
        self.assertFalse(self.merges("command -v gh"))

    def test_an_unparseable_line_falls_back_rather_than_through(self):
        """A line crafted to break the tokeniser must not become one that walks past."""
        self.assertEqual([], shellcmd.commands('echo "unbalanced'))
        self.assertTrue(self.merges('gh pr merge 3 "unbalanced'))

    def test_readers_are_dropped_from_the_acting_set(self):
        self.assertEqual([], shellcmd.acting("echo 'deploy --prod'"))
        self.assertEqual([["fly", "deploy", "--prod"]], shellcmd.acting("fly deploy --prod"))


class TestTheSameShapeOneGateOver(RepoCase):
    """`PRODUCTION_DEPLOY` matched flags anywhere in the line. Not reported — nobody had
    tried to write about a deploy yet — and found by looking for the shape #76 describes."""

    def setUp(self) -> None:
        super().setUp()
        # The migration gate is off below `traction`, so a prototype fixture proves
        # nothing about it either way — the first version of this test asserted a refusal
        # that had never been switched on.
        self.configure(stage_override="traction")

    def bash(self, command: str) -> str:
        proc = subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input=json.dumps({
                "cwd": str(self.repo), "session_id": "s1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": command},
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
        except (json.JSONDecodeError, KeyError, TypeError):
            return "allow"

    def test_writing_about_a_production_deploy_is_not_one(self):
        self.assertNotEqual("deny", self.bash("echo 'we deploy with --production'"))

    def test_actually_deploying_is_still_refused(self):
        self.assertEqual("deny", self.bash("railway up"))
