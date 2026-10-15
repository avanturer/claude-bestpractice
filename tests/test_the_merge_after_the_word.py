"""Issue #268: `+merge` again did not take "UNVERIFIED finish" off a merge, and a green run of
the founder's own `test_command` did not take off the red record of the gate's `pytest`.

`gh pr merge 954 --admin --squash` was refused over two items: "the test suite is red —
recorded for `pytest`, 1h ago; that same command passing clears it", and "this branch carries
an UNVERIFIED finish". The founder was shown it and answered `+merge`. The same merge on the
same head was refused with the same two items, and so was the GitHub tool's.

The red record came from the gate's own run on Stop, made while the pre-push hook's `make ci`
ran beside it. `test_command` is `make test`, and it passed 7488 after that. Nothing it can
print is `pytest`, so the record stood.
"""

from __future__ import annotations

import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase
from test_pullrequest import TranscriptCase

from claude_bestpractice import evidence, store, suites

SIX_TESTS = "".join(f"def test_{n}():\n    assert True\n\n\n" for n in range(6))


class TestTheWordOnAFinishReachesTheMergeItWasGivenFor(TranscriptCase):
    MERGE = "gh pr merge 48 --admin --squash"

    def finish_unverified(self, at: float = 0.0) -> None:
        """A finish filed the way the Stop gate files one: on the commit the branch is at."""
        store.append_jsonl(store.tier_b(self.ctx(), "unverified.jsonl"), {
            "session_id": "s1", "branch": "feat/x", "head": self.ctx().head,
            "reason": "the suite printed no counts", "recorded_at": at or time.time(),
        })

    def an_open_pull_request(self, session_id: str = "s1", finished: bool = True) -> None:
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start(session_id)
        self.open_a_pr(session_id)
        if finished:
            self.finish_unverified()

    def merge(self, session_id: str = "s1"):
        return self.tool("Bash", {"command": self.MERGE}, session_id)

    def refused_over_the_finish(self, session_id: str = "s1") -> None:
        proc = self.merge(session_id)
        self.assertEqual("deny", self.decision(proc), "precondition: the merge is refused")
        self.assertIn("carries an UNVERIFIED finish", self.reason(proc))

    def assert_merges(self, session_id: str = "s1") -> None:
        proc = self.merge(session_id)
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def test_a_word_sent_while_the_session_worked_after_the_refusal_decides_it(self):
        """The word before it covered the merge, so nothing read the transcript for the next."""
        self.an_open_pull_request()
        self.accept()
        self.refused_over_the_finish()
        # A minute on, as a person answers after reading the refusal: two words of theirs
        # within half a minute are the same word, heard twice.
        self.say("+merge", at=time.time() + 60)
        self.assert_merges()

    def test_a_finish_first_filed_on_the_commit_they_were_shown_it_at(self):
        """Refused at the new head over the finish on the commit before; the Stop that ended
        that turn filed it on the new head for the first time, before their word."""
        self.an_open_pull_request()
        self.write("src/app.py", "x = 2\n")
        self.commit("a fix after the finish")
        evidence.record_green(self.ctx(), ["pytest"])
        self.accept()
        self.refused_over_the_finish()
        self.finish_unverified(at=time.time() + 1)
        self.accept()
        self.assert_merges()

    def test_a_commit_they_were_never_shown_a_finish_at_is_not_decided(self):
        """Refused over something else at this commit, the finish filed after: nobody put it
        to them, so their word is not about it (#243)."""
        self.an_open_pull_request(finished=False)
        self.write("notes.py", "draft = 1\n")
        self.accept()
        refused = self.merge()
        self.assertIn("uncommitted changes", self.reason(refused))
        self.assertNotIn("UNVERIFIED", self.reason(refused))
        (self.repo / "notes.py").unlink()
        self.finish_unverified(at=time.time() + 1)
        self.accept()
        self.refused_over_the_finish()

    def test_a_pull_request_this_chat_did_not_open(self):
        """Merged by a chat with none of its own open, whose word is the one merge it allows."""
        self.an_open_pull_request("s1")
        self.start("s2")
        self.accept("s2")
        self.refused_over_the_finish("s2")
        self.accept("s2")
        self.assert_merges("s2")

    def test_a_word_in_another_chat_decides_nothing_shown_here(self):
        self.an_open_pull_request("s1")
        self.start("s2")
        self.accept("s2")
        self.refused_over_the_finish("s2")
        self.start("s3")
        self.accept("s3")
        self.refused_over_the_finish("s2")


class TestTheFoundersCommandAnswersForTheGatesRun(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("tests/test_app.py", SIX_TESTS)
        self.write("Makefile", "test:\n\tpython3 -m pytest -q\n")
        self.commit("six tests")
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed", executed=6)

    def test_their_test_command_passing_clears_the_gates_record(self):
        self.configure(test_command=["make", "test"])
        self.assertTrue(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNone(evidence.red(self.ctx()))
        self.assertEqual("", evidence.red_problem(self.ctx()))

    def test_the_pre_push_hooks_green_does_it(self):
        """What the hook calls once their `make test` has passed."""
        self.configure(test_command=["make", "test"])
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-ci"), "record-green", "make test"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertIn("cleared the recorded failure", proc.stdout, proc.stdout + proc.stderr)

    def test_the_refusal_names_their_command(self):
        self.configure(test_command=["make", "test"])
        said = evidence.red_problem(self.ctx())
        self.assertIn("recorded for `pytest`", said)
        self.assertIn("so does `make test`", said)

    def test_a_command_only_detected_does_not(self):
        """`make test` found by looking at the Makefile is this plugin's guess, not their word."""
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))
        self.assertNotIn("so does", evidence.red_problem(self.ctx()))

    def test_another_recipe_does_not(self):
        self.configure(test_command=["make", "test"])
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "check"]))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_smaller_suite_does_not(self):
        """The failing test deleted, their command passes over what is left."""
        self.configure(test_command=["make", "test"])
        self.write("tests/test_app.py", "def test_0():\n    assert True\n")
        self.commit("drop five tests")
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_suites_own_command_answers_for_its_path(self):
        self.write("backend/tests/test_api.py", SIX_TESTS)
        self.commit("a backend")
        backend = suites.Suite("backend/", ("make", "test"), True)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed", backend, executed=6)
        self.configure(test_commands={"backend/": "make test"})
        self.assertTrue(evidence.clear_red(self.ctx(), ["make", "test"], None, backend))


if __name__ == "__main__":
    unittest.main()
