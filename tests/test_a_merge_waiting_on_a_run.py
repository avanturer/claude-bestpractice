"""A merge the founder's word covers, refused only for want of a run, is the session's to finish.

#381: refused because no run had been seen on the branch, the merge was put to the founder as
theirs to decide, though only a run clears it, and the refusal named no command that makes one.
#382: the run that clears it is the Stop gate's, after the turn, and a green one woke nobody: the
word stood, and the merge waited for the founder to write again, which stops a run in progress.
"""

from __future__ import annotations

from helpers import git
from test_pullrequest import PRCase

from claude_bestpractice import evidence, pullrequest


class WaitingCase(PRCase):
    def finished(self) -> None:
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")

    def stop(self):
        return self.gate("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                           "stop_hook_active": False})

    def refused_for_want_of_a_run(self) -> None:
        self.finished()
        self.start()  # the session's baseline stays where its first start took it
        self.open_a_pr()
        self.accept()
        proc = self.ran("Bash", {"command": f"gh pr merge {self.PR_NUMBER} --squash"})
        self.assertEqual("deny", self.decision(proc))
        self.refusal = self.reason(proc)


class TestNoRunSeenIsTheSessionsToClear(WaitingCase):
    def test_the_refusal_does_not_send_the_session_to_the_founder(self):
        self.refused_for_want_of_a_run()
        self.assertIn(evidence.NEVER_RUN, self.refusal)
        self.assertIn("None of them is the founder's to decide", self.refusal)
        self.assertNotIn("Tell the founder exactly this", self.refusal)

    def test_it_names_what_makes_the_run(self):
        self.refused_for_want_of_a_run()
        self.assertIn("claude-bp-ci local", self.refusal)
        self.assertIn("next finish", self.refusal)

    def test_the_hand_off_says_so_too(self):
        problems = [evidence.unproven(self.ctx(), "feat/x")]
        said = pullrequest.stop_demand({"number": 48, "branch": "feat/x"}, problems)
        self.assertIn("None of them is the founder's to decide", said)
        self.assertNotIn("Report exactly this to the founder", said)

    def test_a_failed_run_is_still_theirs(self):
        failed = ["the last run observed on feat/x FAILED (just now) — `pytest`; nothing has passed since"]
        self.assertFalse(pullrequest.the_sessions_to_clear(failed))


class TestAPassingRunSaysToMerge(WaitingCase):
    def test_once_green_the_finish_says_to_merge_it_once(self):
        self.refused_for_want_of_a_run()
        evidence.record_green(self.ctx(), ["pytest"])
        said = pullrequest.merge_waiting_on_the_run(self.ctx(), "feat/x")
        self.assertIn(f"gh pr merge {self.PR_NUMBER}", said)
        self.assertEqual("", pullrequest.merge_waiting_on_the_run(self.ctx(), "feat/x"))

    def test_nothing_is_said_before_the_run_passes(self):
        self.refused_for_want_of_a_run()
        self.assertEqual("", pullrequest.merge_waiting_on_the_run(self.ctx(), "feat/x"))

    def test_nothing_is_said_without_the_founders_word(self):
        self.finished()
        self.start()
        self.open_a_pr()
        pullrequest.note_refusal(self.ctx(), "feat/x")
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertEqual("", pullrequest.merge_waiting_on_the_run(self.ctx(), "feat/x"))

    def test_nothing_is_said_where_no_merge_was_refused(self):
        self.finished()
        self.start()
        self.open_a_pr()
        self.accept()
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertEqual("", pullrequest.merge_waiting_on_the_run(self.ctx(), "feat/x"))


class TestTheStopGateWakesTheSessionToMerge(WaitingCase):
    def suite_that_passes(self) -> None:
        """Work this session wrote, with a test the gate's own pytest runs and passes."""
        self.start()
        self.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
        self.commit("a test")
        self.claim_a_task("s1", "src/app.py", "tests/test_ok.py")

    def test_its_green_run_ends_in_the_merge(self):
        self.suite_that_passes()
        self.refused_for_want_of_a_run()
        proc = self.stop()
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(f"gh pr merge {self.PR_NUMBER}", proc.stderr)
        self.assertNotIn("Report exactly this to the founder", proc.stderr)

    def test_a_pull_request_handed_off_over_no_run_gets_its_run_and_its_merge(self):
        """Never merged before the finish: the hand-off is what puts it on record."""
        self.suite_that_passes()
        self.finished()
        self.open_a_pr()
        self.accept()
        proc = self.stop()
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(f"gh pr merge {self.PR_NUMBER}", proc.stderr)

    def test_said_once(self):
        self.suite_that_passes()
        self.refused_for_want_of_a_run()
        self.stop()
        self.assertNotIn("gh pr merge", self.stop().stderr)
