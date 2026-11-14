"""A report a session files keeps what it said, and the refusal it was given.

#367 and #368 reached GitHub cut at 300 characters, the crash limit, mid-sentence: #368 lost
the part that said which session was refused and why it held no card, and could not be
reproduced from what was filed. A session's report is the whole of the evidence; a crash
has its frame.
"""

from __future__ import annotations

import os
import subprocess
import sys

from helpers import BIN, RepoCase

from claude_bestpractice import defects, hookio, sessions


class TestAReportKeepsWhatWasSaid(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.configure(report_defects="local")

    def test_a_long_report_is_kept_past_the_crash_limit(self):
        what = "the gate refused a write the card named by its directory; " * 20
        recorded, said = defects.observed(self.ctx(), what, "pre-tool")
        self.assertTrue(recorded, said)
        kept = defects.load(self.ctx())[0]["error"]
        self.assertGreater(len(kept), defects.MAX_ERROR_CHARS)
        self.assertLessEqual(len(kept), defects.MAX_OBSERVATION_CHARS)

    def test_a_crash_is_still_cut_where_it_was(self):
        defects.record(self.ctx(), "pre-tool", KeyError("x" * 1000))
        self.assertEqual(defects.MAX_ERROR_CHARS, len(defects.load(self.ctx())[0]["error"]))

    def test_the_issue_shows_the_gates_own_line_beside_it(self):
        defects.observed(self.ctx(), "word " * 100, "pre-tool",
                         refused="claude-bestpractice: nothing on the board says this session")
        body = defects.body(defects.load(self.ctx())[0])
        self.assertIn("```\nrefused: claude-bestpractice: nothing on the board", body)
        self.assertIn("word " * 99, body)

    def test_the_cli_carries_the_refusal_this_session_was_given(self):
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                        "source": "startup", "cwd": str(self.repo)})
        sessions.touch(self.ctx(), hookio.compose_session_id("s1", str(self.repo)),
                       refusal={"at": 0, "calls": 0, "said": "claude-bestpractice: refused X"})
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-report"), "defect",
             "the gate refused X, which the card covers by its directory"],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
            env={**os.environ, "CLAUDE_CODE_SESSION_ID": "s1"},
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("claude-bestpractice: refused X", defects.load(self.ctx())[0]["refused"])

    def test_outside_a_session_the_report_stands_alone(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-report"), "defect",
             "the gate refused X, which the card covers by its directory"],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
            env={key: value for key, value in os.environ.items()
                 if key != "CLAUDE_CODE_SESSION_ID"},
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("refused", defects.load(self.ctx())[0])
