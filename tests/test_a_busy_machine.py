"""Issue #266: the gate's own pytest run on a machine several sessions share.

The Stop gate called a suite red — "37 failing of 7419 run by the gate itself" — when
all 37 were ERRORs at setup, in three files the branch did not touch, on a machine whose
neighbours were running their own suites. The same files run straight after passed 41 of 41,
and `make check` on the same commit passed all 7419. The report that said which it was had
been deleted with the gate's temp directory, and on the next Stop the failure was repeated
from the record, unchanged, while the project's own run wrote a report of 8228 passed.
"""

from __future__ import annotations

import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import RepoCase

from claude_bestpractice import config, evidence, store, suites, witness

# A fixture that cannot connect the first time it is asked, the way a database under load
# does, and can every time after. The flag is the load; the first run spends it.
FLAKY_SETUP = '''import os

import pytest

FLAG = os.path.join(os.path.dirname(__file__), "..", "busy.flag")


@pytest.fixture
def db():
    if os.path.exists(FLAG):
        os.remove(FLAG)
        raise RuntimeError("OperationalError: could not connect to server")
    return 1


def test_reads(db):
    assert db == 1


def test_writes(db):
    assert db
'''

# The same fixture, always broken: a setup that errors for the code's sake errors again.
BROKEN_SETUP = '''import pytest

DSN = "postgres://billing:Pr0dS3cretPass99@db.prod.example.com/billing"


@pytest.fixture
def db():
    raise RuntimeError(f"OperationalError: could not connect to {DSN}")


def test_reads(db):
    assert db == 1
'''

# An assertion that fails the first time and passes after. A failed assertion is the code
# answering, and the gate never asks it a second time.
FLAKY_ASSERTION = '''import os

FLAG = os.path.join(os.path.dirname(__file__), "..", "busy.flag")


def test_total():
    if os.path.exists(FLAG):
        os.remove(FLAG)
        assert False, "the total was off by one"
'''

PASSING = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"


class GateRunCase(RepoCase):
    def project(self, suite: str, busy: bool = True) -> None:
        self.write("tests/test_db.py", suite)
        self.write("tests/test_other.py", PASSING)
        self.commit("a suite")
        if busy:
            self.write("busy.flag", "")

    def judge(self):
        return evidence._verify_by_running(self.ctx(), [], ["make", "test"], [])

    def kept(self) -> Path:
        return store.tier_a(self.ctx(), evidence.REPORTS_DIR, witness.KEPT_DIR)


class TestAnErrorThatDoesNotComeBackIsNotTheCode(GateRunCase):
    def test_errors_gone_on_a_second_run_are_not_a_failure(self):
        self.project(FLAKY_SETUP)
        verdict = self.judge()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)
        self.assertIn("ERRORed in its first run", verdict.reason)
        self.assertIn("could not connect", verdict.reason, "the error went unnamed")
        self.assertIsNone(evidence.red(self.ctx()), "a failure nobody could reproduce was recorded")

    def test_errors_that_come_back_are_the_failure_they_were(self):
        self.project(BROKEN_SETUP, busy=False)
        verdict = self.judge()
        self.assertFalse(verdict.ok)
        self.assertIn("are ERRORs", verdict.reason)
        self.assertIn("broke again", verdict.reason)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_failed_assertion_is_never_asked_twice(self):
        self.project(FLAKY_ASSERTION)
        verdict = self.judge()
        self.assertFalse(verdict.ok, "an assertion that failed once was run until it passed")
        self.assertNotIn("are ERRORs", verdict.reason)

    def test_a_run_stopped_at_collection_is_not_rerun_into_a_pass(self):
        """pytest stops the session at a collection error, so nothing past it ran: the file
        that errored passing on its own says nothing about the suite it never reached."""
        self.project('import os\n\nFLAG = os.path.join(os.path.dirname(__file__), "..", "busy.flag")\n'
                     'if os.path.exists(FLAG):\n    os.remove(FLAG)\n'
                     '    raise ImportError("half-written module")\n\n\n'
                     'def test_imports():\n    assert True\n')
        self.assertFalse(self.judge().ok)

    def test_without_a_second_run_the_errors_stand(self):
        """No time left for it, or a second run that could not be read: what the first run
        saw is the verdict."""
        self.project(FLAKY_SETUP)
        with mock.patch.object(witness, "_once_more", lambda *args: args[-1]):
            verdict = self.judge()
        self.assertFalse(verdict.ok)
        self.assertIn("are ERRORs", verdict.reason)
        self.assertNotIn("broke again", verdict.reason)

    def test_a_second_run_that_ran_fewer_than_errored_answers_for_none_of_them(self):
        """Two ERRORs, one test passing the second time: the other was never asked again."""
        first = witness.Witnessed(1, 10, 2, "", "pytest", errors=2)
        again = witness.Witnessed(0, 1, 0, "", "pytest")
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(witness, "_spawn", return_value=done), \
                mock.patch.object(witness, "_reported", return_value=again):
            seen = witness._once_more(self.ctx(), [], self.repo / "again.xml", {}, self.repo, 60, first)
        self.assertFalse(seen.passed)
        self.assertEqual(0, seen.recovered)


class TestTheReportOfARedRunOutlivesIt(GateRunCase):
    def test_the_first_error_and_the_kept_report_are_named(self):
        self.project(BROKEN_SETUP, busy=False)
        verdict = self.judge()
        self.assertIn("first: tests.test_db::test_reads", verdict.reason)
        self.assertIn("failed on setup", verdict.reason)
        named = self.kept().relative_to(self.repo).as_posix()
        self.assertIn(f"the gate's report: {named}", verdict.reason)
        self.assertTrue(list(self.kept().glob("*.xml")), "nothing was kept")

    def test_what_it_keeps_holds_no_password(self):
        self.project(BROKEN_SETUP, busy=False)
        self.judge()
        for report in self.kept().glob("*.xml"):
            text = report.read_text(encoding="utf-8")
            self.assertIn("could not connect", text)
            self.assertNotIn("Pr0dS3cretPass99", text)

    def test_git_never_sees_it(self):
        self.project(BROKEN_SETUP, busy=False)
        self.judge()
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                cwd=self.repo, capture_output=True, text=True).stdout
        self.assertNotIn(witness.KEPT_DIR, status)

    def test_a_run_that_passes_takes_the_old_one_away(self):
        self.project(BROKEN_SETUP, busy=False)
        self.judge()
        self.write("tests/test_db.py", PASSING)
        self.commit("fixed")
        self.assertTrue(self.judge().ok)
        self.assertFalse(self.kept().exists(), "a red report outlived the suite going green")

    def test_it_is_not_read_back_as_the_projects_report(self):
        self.project(BROKEN_SETUP, busy=False)
        self.judge()
        self.assertEqual([], [path for path in evidence._reports(
            self.ctx(), list(config.DEFAULT_ARTIFACT_GLOBS)) if witness.KEPT_DIR in path.parts])


class TestARecordedFailureGivesWayToAPassingReport(RepoCase):
    """The comment on #266: 166 ERRORs repeated from the record at every Stop, "it was not run
    again", while `make test` on the same commit wrote a report of 8228 passed. A report is not
    a run this gate witnessed, so it clears nothing; it only sends the suite back to be run."""

    PASSES = ["python3", "-c", "print('1 passed in 0.1s')"]

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.write("tests/test_app.py", "def test_a():\n    pass\n")
        self.commit("the app")
        evidence.record_red(self.ctx(), list(self.PASSES), "1 failed", None,
                            evidence.tree_hash(self.ctx()))

    def report(self, passed: bool, age: float = 0.0) -> None:
        path = evidence.report_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        failures = 0 if passed else 1
        path.write_text(f'<testsuite tests="1" failures="{failures}" errors="0" skipped="0">'
                        '<testcase classname="t" name="a"/></testsuite>', encoding="utf-8")
        stamp = time.time() + (-age if age else 1)
        os.utime(path, (stamp, stamp))

    def verdict(self):
        self.configure(test_command=self.PASSES)
        cfg = config.load(self.ctx())
        plan = suites.for_changes(self.ctx(), cfg, ["src/app.py"])
        return evidence.verify(self.ctx(), cfg.artifact_globs, ["src/app.py"], cfg.test_command, plan)

    def test_a_report_of_a_pass_since_runs_it_for_real(self):
        self.report(passed=True)
        verdict = self.verdict()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertNotIn("not run", verdict.reason)

    def test_a_report_of_a_failure_since_does_not(self):
        self.report(passed=False)
        self.assertIn("not run", self.verdict().reason)

    def test_a_report_older_than_the_failure_does_not(self):
        self.report(passed=True, age=evidence.REASSERT_SECONDS / 2)
        self.assertIn("not run", self.verdict().reason)

    def test_the_report_itself_clears_nothing(self):
        """Run again, the suite still fails, and the report that sent it back changes nothing."""
        self.write("tests/test_app.py", "def test_a():\n    assert False\n")
        self.commit("a test that fails")
        evidence.record_red(self.ctx(), ["pytest"], "1 failed", None, evidence.tree_hash(self.ctx()))
        self.report(passed=True)
        verdict = self.verdict()
        self.assertFalse(verdict.ok)
        self.assertNotIn("not run", verdict.reason, "the suite was not run again")
        self.assertIsNotNone(evidence.red(self.ctx()))


class TestTheGatesOwnRunKeepsItsCacheOutOfTheTree(RepoCase):
    def test_no_pytest_cache_is_left_in_the_repository(self):
        """The cache that lets the gate run what errored again is its own, in its temp dir.
        With the repository's own `pytest.ini` pinned, pytest kept it at the top of the tree."""
        self.write("pytest.ini", "[pytest]\n")
        self.write("tests/test_a.py", PASSING)
        self.commit("tests")
        self.assertTrue(evidence._verify_by_running(self.ctx(), [], ["make", "test"], []).ok)
        self.assertEqual([], list(self.repo.rglob(".pytest_cache")))


if __name__ == "__main__":
    unittest.main()
