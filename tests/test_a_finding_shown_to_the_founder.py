"""Issue #283: the merge gate refused after the founder's `+merge` over a review finding.

The review flagged a new test carrying `@pytest.mark.skipif(not _db_ok(), reason="требуется
Postgres")` as `skipped-test`. The merge refusal listed it and promised the merge "as soon as
the list above is empty". The founder read it and answered `+merge`, and the same merge was
refused with the same list: nothing but rewriting correct code emptied it.
"""

from __future__ import annotations

import time

from helpers import git
from test_pullrequest import TRIGGER, TranscriptCase

from claude_bestpractice import board, evidence, pullrequest, reviewrules

FINDING = "1 review finding(s): sql-interpolation in src/app.py"


class TestAGuardOnTheEnvironmentIsNotASkippedTest(TranscriptCase):
    def test_skipif_with_a_condition_does_not_fire(self):
        self.write("tests/test_ops.py",
                   "import pytest\n\n\n@pytest.mark.skipif(not _db_ok(), reason=\"требуется "
                   "Postgres\")\ndef test_scrape():\n    assert True\n")
        self.assertFalse(reviewrules.still_fires(self.repo, "skipped-test", "tests/test_ops.py"))

    def test_a_condition_that_is_always_true_still_switches_the_test_off(self):
        self.write("tests/test_ops.py",
                   "import pytest\n\n\n@pytest.mark.skipif(True, reason=\"flaky\")\n"
                   "def test_scrape():\n    assert True\n")
        self.assertTrue(reviewrules.still_fires(self.repo, "skipped-test", "tests/test_ops.py"))


class TestTheWordAfterTheRefusalTakesTheFindingOff(TranscriptCase):
    MERGE = "gh pr merge 48 --admin --squash"

    def a_pull_request_with_a_finding(self, session_id: str = "s1") -> None:
        self.write("src/app.py", TRIGGER)
        self.commit("interpolate")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start(session_id)
        self.open_a_pr(session_id)
        board.add_open_item(self.ctx(), item_id="review-s1-1", text=FINDING,
                            branch=self.ctx().branch, session_id=session_id,
                            subject_paths=["src/app.py"])

    def merge(self, session_id: str = "s1"):
        return self.tool("Bash", {"command": self.MERGE}, session_id)

    def refused_over_the_finding(self, session_id: str = "s1") -> str:
        proc = self.merge(session_id)
        self.assertEqual("deny", self.decision(proc), "precondition: the merge is refused")
        self.assertIn("sql-interpolation in src/app.py", self.reason(proc))
        return self.reason(proc)

    def test_the_refusal_names_the_word_that_decides_it(self):
        self.a_pull_request_with_a_finding()
        self.accept()
        reason = self.refused_over_the_finding()
        self.assertIn("a `+merge` they send after seeing this takes it off", reason)

    def test_a_word_said_after_the_refusal_lets_the_merge_through(self):
        self.a_pull_request_with_a_finding()
        self.accept()
        self.refused_over_the_finding()
        self.accept()
        proc = self.merge()
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def test_a_word_sent_while_the_session_worked_is_read_back(self):
        self.a_pull_request_with_a_finding()
        self.accept()
        self.refused_over_the_finding()
        self.say("+merge", at=time.time() + 60)
        proc = self.merge()
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def test_a_word_said_before_anybody_showed_it_decides_nothing(self):
        self.a_pull_request_with_a_finding()
        self.accept()
        self.assertEqual([FINDING], pullrequest.findings_in(
            pullrequest.blockers(self.ctx(), "main")))

    def test_a_finding_the_refusal_did_not_list_is_still_put_to_them(self):
        self.a_pull_request_with_a_finding()
        self.accept()
        self.refused_over_the_finding()
        self.accept()
        self.write("src/other.py", TRIGGER)
        git(["add", "-A"], self.repo)
        self.commit("more interpolation")
        evidence.record_green(self.ctx(), ["pytest"])
        board.add_open_item(self.ctx(), item_id="review-s1-2",
                            text="1 review finding(s): sql-interpolation in src/other.py",
                            branch=self.ctx().branch, session_id="s1",
                            subject_paths=["src/other.py"])
        self.assertEqual(["1 review finding(s): sql-interpolation in src/other.py"],
                         pullrequest.findings_in(pullrequest.blockers(self.ctx(), "main")))
