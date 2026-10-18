"""The Stop gate checks after the turn. Run while it held the turn, the suite kept the founder out
of the session for all of it in a client that takes no message while a hook runs, and the stop for
their message (#277) waited on a message they could not send."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from helpers import BIN, RepoCase
from test_the_founder_speaks import a_session_over_a_slow_suite, appended

from claude_bestpractice import evidence, founder

LIB = BIN.parent / "lib"
SAID = {"type": "assistant", "message": {"content": [{"type": "text", "text": "On it."}]}}


def stamped(entry: dict, at: float) -> str:
    """A transcript line as the harness writes it, stamped at `at`."""
    stamp = datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="milliseconds")
    return json.dumps({**entry, "timestamp": stamp.replace("+00:00", "Z")}) + "\n"


def asked(*calls: str) -> subprocess.CompletedProcess:
    """A gate process that asks the session something, with `calls` made first."""
    code = "from claude_bestpractice import hookio\n" + "".join(f"{call}\n" for call in calls) \
        + "hookio.ask('Write down what you learned.', note='asked for notes')\n"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(LIB)}, timeout=60)


class TestClaudeCodeRunsItAfterTheTurn(unittest.TestCase):
    def test_in_the_background_and_waking_the_session(self):
        manifest = json.loads((BIN.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        hooks = [hook for group in manifest["hooks"]["Stop"] for hook in group["hooks"]]
        self.assertEqual(1, len(hooks))
        self.assertTrue(hooks[0].get("asyncRewake"))
        self.assertTrue(hooks[0]["command"].endswith('/bin/evidence-gate-after-the-turn"'))

    def test_a_question_after_the_turn_wakes_the_session(self):
        """Read only with the next turn, a question about notes or about a removed tree reached
        nobody: there is no turn left to continue, and only an exit of 2 wakes the session."""
        proc = asked("hookio.after_the_turn()")
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Write down what you learned.", proc.stderr)

    def test_in_the_turn_it_is_feedback_as_before(self):
        proc = asked()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Write down what you learned.",
                      json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"])


class TestAfterTheTurnTheSessionCounts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-test-"))
        self.transcript = self.tmp / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")

    def add(self, entry: dict, at: float) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(stamped(entry, at))

    def listener(self, after_the_turn: bool = True) -> founder.Listener:
        return founder.Listener(str(self.transcript), after_the_turn=after_the_turn)

    def test_the_session_at_work_again_is_heard(self):
        listening = self.listener()
        self.add(SAID, time.time() + 1)
        self.assertTrue(listening.heard())

    def test_the_founders_message_to_an_idle_session_is_heard(self):
        """An idle session takes their message as a turn of its own, with nothing queued."""
        listening = self.listener()
        self.add({"type": "user", "message": {"role": "user", "content": "+merge"}}, time.time() + 1)
        self.assertTrue(listening.heard())

    def test_the_turns_own_last_message_written_late_is_not(self):
        """The harness can write the turn's last message after the Stop began; it is stamped
        before, and taken for new work it would stop every run."""
        listening = self.listener()
        self.add(SAID, time.time() - 5)
        self.assertFalse(listening.heard())

    def test_a_tool_result_is_not_the_founder(self):
        listening = self.listener()
        self.add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}}, time.time() + 1)
        self.assertFalse(listening.heard())

    def test_while_the_turn_is_held_only_a_queued_message_counts(self):
        listening = self.listener(after_the_turn=False)
        self.add(SAID, time.time() + 1)
        self.add({"type": "user", "message": {"role": "user", "content": "+merge"}}, time.time() + 1)
        self.assertFalse(listening.heard())


class TestTheRunStopsWhenTheSessionGoesOn(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.transcript = a_session_over_a_slow_suite(self)
        self.write("app.py", "VERSION = 1\n")
        self.commit("change 1")

    def test_the_run_is_stopped_and_recorded_as_cut_short(self):
        """Woken by a task's notification, the session edits the tree the run is judging."""
        timer = appended(self.transcript, stamped(SAID, time.time() + 5), 1.0)
        began = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(BIN / "evidence-gate-after-the-turn")],
            input=json.dumps({"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo),
                              "stop_hook_active": False, "transcript_path": str(self.transcript)}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
            env={**os.environ, "SLOW_TEST_SECONDS": "60"})
        timer.join()
        self.assertLess(time.monotonic() - began, 30, "the run went on under the session's work")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue((evidence.last_run(self.ctx()) or {}).get("cut_short"))


if __name__ == "__main__":
    unittest.main()
