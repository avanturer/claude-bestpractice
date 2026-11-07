"""What the founder sees beside the chat: the board as one status line and a pane (decision 0076).

The hooks module draws it under the prompt, in the terminal and in VS Code, and never sends it
to the model. The library answers what it says, through `bin/claude-bp-mod board`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from unittest import mock

from helpers import BIN, git, sid

from claude_bestpractice import board, evidence, plan, pullrequest, store

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
        self.assertIn(f"suite: `pytest` fails on {self.ctx().branch}, red just now", seen["rows"])

    def red_record(self, **fields) -> None:
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        path = store.tier_a(self.ctx(), evidence.RED_SUITE_FILE)
        store.write_json(path, {**store.read_json(path), **fields})

    def test_a_failure_long_unrun_on_other_code_is_history_not_red(self):
        """A founder's main checkout said "failing for 29d" over a run on code 103 commits back:
        `red_line` calls that unconfirmed, and the line under the prompt goes by it."""
        self.start("s1")
        two_days_ago = time.time() - 2 * 86400
        self.red_record(first_seen=two_days_ago, last_seen=two_days_ago, tree_hash="0" * 40)

        seen = board.glance(self.ctx(), "s1")

        self.assertNotIn("suite red", seen["line"])
        self.assertIn("suite: `pytest` failed 2d ago on other code, and has not run here since",
                      seen["rows"])

    def test_a_run_beside_a_neighbours_database_is_not_called_red(self):
        self.start("s1")
        self.red_record(shared_with="fuddy-ota", tree=str(self.repo))

        seen = board.glance(self.ctx(), "s1")

        self.assertNotIn("suite red", seen["line"])
        self.assertTrue(any("shares its database with fuddy-ota" in row for row in seen["rows"]),
                        seen["rows"])

    def test_the_ledger_is_read_once_whatever_the_number_of_sessions(self):
        for chat in ("s1", "s2", "s3", "s4"):
            self.start(chat)
        with mock.patch.object(plan, "load_all", wraps=plan.load_all) as read:
            board.glance(self.ctx(), "s1")
        self.assertEqual(1, read.call_count)

    def test_reading_the_tree_for_the_glance_leaves_the_index_as_it_was(self):
        """`git status` writes the index back under `index.lock`; read every minute in every
        session, it failed a commit made in that same moment."""
        self.start("s1")
        self.write("feature.py", "x = 1\n")
        self.commit("feature")
        later = time.time() + 60
        os.utime(self.repo / "feature.py", (later, later))
        index = self.repo / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        two_days_ago = time.time() - 2 * 86400
        self.red_record(first_seen=two_days_ago, last_seen=two_days_ago, tree_hash="0" * 40)

        board.glance(self.ctx(), "s1")
        evidence.tree_hash(self.ctx())

        self.assertEqual(before, (index.read_bytes(), index.stat().st_mtime_ns))
        git(["status", "--porcelain"], self.repo)
        self.assertNotEqual(before[1], index.stat().st_mtime_ns,
                            "a plain status does rewrite it, so the check can fail")

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
