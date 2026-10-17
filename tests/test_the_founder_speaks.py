"""#277. The Stop gate ran the suite while the founder's message waited behind it: five minutes
of pytest and a minute of jest before their `+merge` was read, or until they pressed Esc."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import RepoCase

from claude_bestpractice import delivery, evidence, founder, witness


def enqueued(text: str) -> str:
    """A message queued while the session was busy, as the harness files it (2.1.302)."""
    return json.dumps({"type": "queue-operation", "operation": "enqueue",
                       "timestamp": "2026-10-17T21:58:33.988Z", "sessionId": "s",
                       "content": text}) + "\n"


NOTIFICATION = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
                "</task-notification>")


def appended(path: Path, text: str, after: float) -> threading.Timer:
    """`text` added to the end of `path` in `after` seconds, the way the harness appends."""
    def write() -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    timer = threading.Timer(after, write)
    timer.start()
    return timer


class TestTheListenerHearsOnlyTheFounder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-test-"))
        self.transcript = self.tmp / "transcript.jsonl"
        self.transcript.write_text(json.dumps({"type": "user", "message": {"content": "go"}})
                                   + "\n", encoding="utf-8")

    def add(self, text: str) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def test_a_message_typed_after_it_began_is_heard(self):
        listening = founder.Listener(str(self.transcript))
        self.assertFalse(listening.heard())
        self.add(enqueued("+merge"))
        self.assertTrue(listening.heard())

    def test_one_queued_before_it_began_is_not(self):
        self.add(enqueued("+merge"))
        self.assertFalse(founder.Listener(str(self.transcript)).heard())

    def test_a_notification_and_a_message_of_ours_are_not(self):
        listening = founder.Listener(str(self.transcript))
        self.add(enqueued(NOTIFICATION) + enqueued("[claude-bp] the suite is RED on main")
                 + json.dumps({"type": "queue-operation", "operation": "dequeue"}) + "\n")
        self.assertFalse(listening.heard())

    def test_a_line_half_written_is_read_once_it_ends(self):
        listening = founder.Listener(str(self.transcript))
        line = enqueued("стоп, не то")
        self.add(line[:30])
        self.assertFalse(listening.heard())
        self.add(line[30:])
        self.assertTrue(listening.heard())

    def test_no_transcript_says_nothing(self):
        for path in ("", str(self.tmp / "gone.jsonl")):
            with self.subTest(path=path):
                self.assertFalse(founder.Listener(path).heard())


class TestARunStopsForTheFounder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-test-"))

    def test_the_run_is_stopped_and_what_it_started_with_it(self):
        pid_file = self.tmp / "child.pid"
        spoken = threading.Event()
        threading.Timer(0.5, spoken.set).start()
        began = time.monotonic()
        with witness.until_spoken(spoken.is_set), self.assertRaises(witness.FounderSpoke):
            witness.run_bounded(["sh", "-c", f"sleep 30 & echo $! > {pid_file}; wait"],
                                self.tmp, dict(os.environ), 60)
        self.assertLess(time.monotonic() - began, 10)
        child = int(pid_file.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            for _ in range(50):
                os.kill(child, 0)
                time.sleep(0.1)

    def test_none_starts_once_they_have_spoken(self):
        started = self.tmp / "started"
        with witness.until_spoken(lambda: True), self.assertRaises(witness.FounderSpoke):
            witness.run_bounded(["touch", str(started)], self.tmp, dict(os.environ), 60)
        self.assertFalse(started.exists())

    def test_with_no_one_listening_a_run_ends_as_it_did(self):
        proc = witness.run_bounded(["sh", "-c", "echo said; exit 3"], self.tmp,
                                   dict(os.environ), 60)
        self.assertEqual((3, "said\n"), (proc.returncode, proc.stdout))
        with self.assertRaises(witness.RanOutOfTime):
            witness.run_bounded(["sleep", "5"], self.tmp, dict(os.environ), 1)

    def test_a_listener_that_fails_has_heard_nothing(self):
        def broken() -> bool:
            raise OSError("the transcript went away")
        with witness.until_spoken(broken):
            proc = witness.run_bounded(["sh", "-c", "sleep 1; exit 0"], self.tmp,
                                       dict(os.environ), 60)
        self.assertEqual(0, proc.returncode)


# A suite as slow as the environment says, so one project serves every case below.
SLOW_TEST = (
    "import os\nimport time\nimport unittest\n\n\n"
    "class TestSlow(unittest.TestCase):\n"
    "    def test_takes_its_time(self):\n"
    "        time.sleep(float(os.environ.get('SLOW_TEST_SECONDS') or 0))\n"
)


class TestTheStopGateLetsTheFounderIn(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.configure(require_task=False, manage_pull_requests=False, test_command=[
            "python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        self.write("tests/__init__.py", "")
        self.write("tests/test_slow.py", SLOW_TEST)
        self.commit("a suite that takes its time")
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                        "source": "startup"})
        self.transcript = self.tmp / "transcript.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.edits = 0

    def work(self) -> None:
        """A change of the session's own, for the Stop to verify."""
        self.edits += 1
        self.write("app.py", f"VERSION = {self.edits}\n")
        self.commit(f"change {self.edits}")

    def stop(self, slow: float, said: str = "", after: float = 1.0):
        timer = appended(self.transcript, enqueued(said), after) if said else None
        began = time.monotonic()
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False,
             "transcript_path": str(self.transcript)},
            env={**os.environ, "SLOW_TEST_SECONDS": str(slow)})
        if timer is not None:
            timer.join()
        return proc, time.monotonic() - began

    def test_a_message_typed_while_the_suite_runs_ends_the_turn_at_once(self):
        self.work()
        proc, took = self.stop(slow=60, said="+merge")
        self.assertLess(took, 30, "the suite ran on while the founder waited")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn('"block"', proc.stdout)
        self.assertIn("the founder wrote while the suite ran", proc.stderr)
        self.assertTrue((evidence.last_run(self.ctx()) or {}).get("cut_short"))

    def test_a_green_from_before_does_not_stand_for_the_work_it_stopped_judging(self):
        self.work()
        proc, _ = self.stop(slow=0)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("", evidence.unproven(self.ctx()), "precondition: observed green")
        self.work()
        self.stop(slow=60, said="+merge")
        said = evidence.unproven(self.ctx())
        self.assertIn("because the founder wrote while it ran", said)
        self.assertIn(said, delivery.ready(self.ctx(), "main"))
        self.assertIn("stopped when the founder wrote", delivery._test_health(self.ctx()))
        proc, _ = self.stop(slow=0)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("", evidence.unproven(self.ctx()), "the next run that passes clears it")

    def test_nor_is_it_held_for_what_a_finish_is_asked(self):
        """The notes a clean finish is asked for once would hold the turn their message waits
        behind, over work nothing verified."""
        self.configure(notes_after_calls=1)
        self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                   "tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.work()
        proc, _ = self.stop(slow=60, said="+merge")
        self.assertNotIn("additionalContext", proc.stdout)
        proc, _ = self.stop(slow=0)
        self.assertIn("additionalContext", proc.stdout, "precondition: a finish is asked")

    def test_a_notification_does_not_stop_it(self):
        self.work()
        proc, took = self.stop(slow=3, said=NOTIFICATION, after=0.5)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertGreaterEqual(took, 3)
        self.assertFalse((evidence.last_run(self.ctx()) or {}).get("cut_short"))
        self.assertEqual("", evidence.unproven(self.ctx()))


if __name__ == "__main__":
    unittest.main()
