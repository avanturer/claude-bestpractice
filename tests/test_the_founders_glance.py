"""What the founder sees beside the chat: the board as one status line and a pane (decision 0074).

The hooks module draws it under the prompt, in the terminal and in VS Code, and never sends it
to the model. The library answers what it says, through `bin/claude-bp-mod board`.
"""

from __future__ import annotations

import json
import subprocess
import sys

from helpers import BIN, sid

from claude_bestpractice import board, evidence, plan, pullrequest

from test_gates import GateCase


class TestTheGlance(GateCase):
    def test_another_session_is_named_with_its_card_and_branch(self):
        self.start("s1")
        self.start("s2")
        card = plan.add(self.ctx(), "Add CSV export", paths=["src/csv.py"], done_when="exported")
        plan.claim(self.ctx(), card.id, sid(self.repo, "s2"), self.ctx().branch)

        seen = board.glance(self.ctx(), "s1")

        self.assertIn("1 other session(s)", seen["line"])
        self.assertTrue(any(f"{card.id} Add CSV export" in row for row in seen["rows"]), seen["rows"])

    def test_a_pull_request_waiting_for_the_founder_is_named_by_its_number(self):
        self.start("s1")
        pullrequest.opened(self.ctx(), "feat/export", "main", "s1", number=297)

        seen = board.glance(self.ctx(), "s1")

        self.assertIn("#297 wait for your `+merge`", seen["line"])
        self.assertIn("#297 on feat/export: ready to merge", seen["rows"])

    def test_a_draft_is_not_waiting_for_anyone(self):
        self.start("s1")
        pullrequest.opened(self.ctx(), "feat/later", "main", "s1", number=12, draft=True)
        self.assertNotIn("#12", board.glance(self.ctx(), "s1")["line"])

    def test_a_red_suite_is_said(self):
        self.start("s1")
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")

        seen = board.glance(self.ctx(), "s1")

        self.assertIn("suite red", seen["line"])
        self.assertTrue(seen["rows"], "the pane names the red suite")

    def test_alone_it_says_so(self):
        self.start("s1")
        self.assertEqual("bestpractice · no other session", board.glance(self.ctx(), "s1")["line"])


class TestTheBridge(GateCase):
    def ask(self, cwd: str) -> dict:
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-mod"), "board"],
                              input=json.dumps({"cwd": cwd, "session": "s1"}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def test_the_module_is_answered_from_the_repository(self):
        self.start("s1")
        self.assertIn("line", self.ask(str(self.repo)))

    def test_switched_off_the_line_is_cleared(self):
        """Off means off (decision 0016): an empty answer clears what the module shows."""
        self.configure(enabled=False)
        self.assertEqual({}, self.ask(str(self.repo)))
