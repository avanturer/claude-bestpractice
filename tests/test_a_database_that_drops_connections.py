"""Issue #289: the gate's own pytest on a database that drops connections was filed as a red suite.

The Stop gate ran bare `pytest` in `backend/` on the shared database at 127.0.0.1:5432, which on
WSL goes through Docker's proxy and drops connections. Whole files ERRORed at setup with
`psycopg.OperationalError: server closed the connection unexpectedly`, in different files on every
run: 1 failure, then 150 errors, then 146. The record held a merge the founder had said `+merge`
to, and only a pass on that database could clear it. The project's own run on its own database
passed 11162.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from helpers import RepoCase
from test_a_busy_machine import BROKEN_SETUP, PASSING, GateRunCase

from claude_bestpractice import evidence, migrate, store, witness

# A fixture whose server drops the connection every time it is asked, as libpq says it.
DROPPED = '''import pytest


class OperationalError(Exception):
    pass


@pytest.fixture
def db():
    raise OperationalError("server closed the connection unexpectedly\\n"
                           "\\tThis probably means the server terminated abnormally")


def test_reads(db):
    assert db == 1


def test_writes(db):
    assert db
'''

# Dropped the first time, broken for the code's sake the second.
DROPPED_THEN_BROKEN = '''import os

import pytest

FLAG = os.path.join(os.path.dirname(__file__), "..", "busy.flag")


@pytest.fixture
def db():
    if os.path.exists(FLAG):
        os.remove(FLAG)
        raise ConnectionRefusedError("[Errno 111] Connection refused")
    raise KeyError("DATABASE_NAME")


def test_reads(db):
    assert db == 1
'''

# A test the database dropped, beside an assertion the code failed.
DROPPED_BESIDE_A_FAILURE = DROPPED + '''

def test_total():
    assert 1 + 1 == 3
'''

# A connection lost in the test's body, which pytest reports as a FAILURE and not an ERROR: the
# server dropped it at the first query rather than when a fixture connected (#339, #355).
LOST_IN_THE_BODY = '''

class ServerGone(Exception):
    pass


def test_queries():
    raise ServerGone("server closed the connection unexpectedly")
'''

# Lost in the body the first time, and answering the second.
LOST_IN_THE_BODY_ONCE = '''import os

FLAG = os.path.join(os.path.dirname(__file__), "..", "busy.flag")


def test_queries():
    if os.path.exists(FLAG):
        os.remove(FLAG)
        raise ConnectionResetError(104, "Connection reset by peer")
'''


class TestADroppedConnectionIsNoVerdictOnTheCode(GateRunCase):
    def test_the_report(self):
        self.project(DROPPED, busy=False)
        verdict = self.judge()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertTrue(verdict.unverified, "a run that reached no code was called a pass")
        self.assertIn("connection to a server they use was lost or refused", verdict.reason)
        self.assertIn("broke the same way when the gate ran them again", verdict.reason)
        self.assertIn("server closed the connection unexpectedly", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()), "a dropped connection was filed as the code")
        self.assertIsNone(evidence.last_green(self.ctx()), "tests that ran nowhere were green")

    def test_a_red_record_it_did_not_write_stands(self):
        """It is no verdict, so it clears nothing either: a failure of the code seen before
        is still unrun."""
        self.project(BROKEN_SETUP, busy=False)
        self.assertFalse(self.judge().ok)
        self.write("tests/test_db.py", DROPPED)
        self.commit("the database went")
        self.assertTrue(self.judge().unverified)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_another_error_beside_it_is_the_failure_it_was(self):
        self.project(DROPPED + BROKEN_SETUP.replace("def db():", "def db2():")
                     .replace("(db)", "(db2)").replace("test_reads", "test_reads_too")
                     .replace("import pytest\n", ""), busy=False)
        self.assertFalse(self.judge().ok)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_failed_assertion_beside_it_is_the_failure_it_was(self):
        self.project(DROPPED_BESIDE_A_FAILURE, busy=False)
        self.assertFalse(self.judge().ok)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_one_that_breaks_otherwise_the_second_time_is_the_failure_it_was(self):
        self.project(DROPPED_THEN_BROKEN)
        verdict = self.judge()
        self.assertFalse(verdict.ok, verdict.reason)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_one_that_passes_the_second_time_is_a_pass_as_before(self):
        """Decision 0034 stands: an ERROR a second run does not see is not the code's."""
        self.project(DROPPED_THEN_BROKEN.replace('raise KeyError("DATABASE_NAME")', "return 1"))
        verdict = self.judge()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)

    def test_without_a_second_run_it_is_still_no_verdict(self):
        """No time left to run them again: a connection that was lost said nothing about the
        code the first time either."""
        self.project(DROPPED, busy=False)
        with mock.patch.object(witness, "_once_more", lambda *args: args[-1]):
            verdict = self.judge()
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertNotIn("broke the same way", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_its_report_is_kept(self):
        self.project(DROPPED, busy=False)
        self.judge()
        self.assertTrue(list(self.kept().glob("*.xml")))


class TestAConnectionLostInATestsBodyIsNoVerdictEither(GateRunCase):
    """#339, #355: pytest calls a connection lost in the test's body a FAILURE, not an ERROR, and
    one such FAILURE beside 54 ERRORs had the whole run filed red, with no second run. What the
    exception says decides, not pytest's phase; a failed assertion is never a lost connection."""

    def test_alone(self):
        self.project(LOST_IN_THE_BODY, busy=False)
        verdict = self.judge()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertIn("all 1 test(s) that broke", verdict.reason)
        self.assertIn("broke the same way when the gate ran them again", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()), "a dropped connection was filed as the code")

    def test_beside_errors_at_setup(self):
        """The shape of #355: ERRORs at setup and one FAILURE, every one the server's."""
        self.project(DROPPED + LOST_IN_THE_BODY, busy=False)
        verdict = self.judge()
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertIn("all 3 test(s) that broke", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_one_that_passes_the_second_time_is_a_pass(self):
        """Decision 0034's second run, given to it as to an ERROR."""
        self.project(LOST_IN_THE_BODY_ONCE)
        verdict = self.judge()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)
        self.assertIn("1 of them ERRORed in its first run or lost a connection", verdict.reason)
        self.assertIn("Connection reset by peer", verdict.reason)

    def test_an_assertion_that_quotes_one_is_the_failure_it_was(self):
        self.project(DROPPED + '\n\ndef test_log():\n    log = "Connection refused"\n'
                     '    assert "Connection refused" not in log\n', busy=False)
        self.assertFalse(self.judge().ok)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_an_exception_of_the_codes_is_never_asked_twice(self):
        """Raised in the body and gone the second time: the code answered, and asking it again
        is asking for a different answer."""
        self.project(LOST_IN_THE_BODY_ONCE.replace(
            'ConnectionResetError(104, "Connection reset by peer")', 'KeyError("total")'))
        verdict = self.judge()
        self.assertFalse(verdict.ok, "an exception of the code's was run until it passed")
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_run_that_exited_1_with_nothing_broken_lost_nothing(self):
        self.assertFalse(witness.Witnessed(1, 5, 0, "", "pytest").lost_connection)

    def test_several_projects_count_what_answered_together(self):
        one = witness.Witnessed(1, 3, 1, "", "pytest", answered=1)
        other = witness.Witnessed(1, 3, 1, "", "pytest", disconnected=1)
        self.assertEqual(1, witness._together(self.repo, [(self.repo, one), (self.repo / "b", other)])
                         .answered)


class TestWhatCountsAsADroppedConnection(unittest.TestCase):
    def report(self, message: str, text: str = "", tag: str = "error") -> Path:
        import shutil
        import tempfile
        from xml.sax.saxutils import escape, quoteattr

        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        path = home / "report.xml"
        errors, failures = (1, 0) if tag == "error" else (0, 1)
        path.write_text(
            f'<testsuites><testsuite tests="1" errors="{errors}" failures="{failures}" skipped="0">'
            f'<testcase classname="t" name="test_a"><{tag} message={quoteattr(message)}>'
            f"{escape(text)}</{tag}>"
            "</testcase></testsuite></testsuites>")
        return path

    def test_what_the_drivers_and_the_socket_say(self):
        for message in (
                'failed on setup with "psycopg.OperationalError: server closed the connection '
                'unexpectedly"',
                'failed on setup with "psycopg2.OperationalError: connection to server at '
                '"db" (10.0.0.5), port 5432 failed: timeout expired"',
                'connection to server on socket "/run/postgresql/.s.PGSQL.5432" failed: No such '
                'file or directory',
                'could not connect to server: Connection refused',
                'failed on setup with "ConnectionResetError: [Errno 104] Connection reset by peer"',
                'SSL SYSCALL error: EOF detected',
                "(2013, 'Lost connection to MySQL server during query')"):
            with self.subTest(message=message):
                self.assertEqual(1, evidence.parse_artifact(self.report(message)).disconnected)

    def test_a_traceback_that_only_quotes_one_is_not(self):
        path = self.report('failed on setup with "KeyError: DATABASE_NAME"',
                           "    # retry when the server says connection refused\nKeyError")
        self.assertEqual(0, evidence.parse_artifact(path).disconnected)

    def test_a_failed_assertion_is_not_whatever_it_quotes(self):
        """As pytest writes one: named, unnamed where it rewrote the assert, `pytest.fail`, and a
        fixture's own assertion at setup."""
        for tag, message in (
                ("failure", "AssertionError: Connection refused\nassert False"),
                ("failure", "assert 'Connection refused' not in 'Connection refused'"),
                ("failure", "Failed: connection refused by the server"),
                ("error", 'failed on setup with "AssertionError: server closed the connection '
                          'unexpectedly"')):
            with self.subTest(message=message):
                artifact = evidence.parse_artifact(self.report(message, tag=tag))
                self.assertEqual(0, artifact.disconnected)
                self.assertEqual(int(tag == "failure"), artifact.answered)

    def test_one_the_driver_raised_in_a_tests_body_is(self):
        """A FAILURE, where pytest names the exception first: #339's shape."""
        for message in ("psycopg.OperationalError: server closed the connection unexpectedly",
                        "ConnectionRefusedError: [Errno 111] Connection refused"):
            with self.subTest(message=message):
                artifact = evidence.parse_artifact(self.report(message, tag="failure"))
                self.assertEqual(1, artifact.disconnected)
                self.assertEqual(0, artifact.answered)

    def test_any_other_failure_is_the_code_answering_and_an_error_is_not(self):
        failed = evidence.parse_artifact(self.report("KeyError: 'total'", tag="failure"))
        self.assertEqual((0, 1), (failed.disconnected, failed.answered))
        errored = evidence.parse_artifact(self.report('failed on setup with "KeyError: x"'))
        self.assertEqual((0, 0), (errored.disconnected, errored.answered))


class TestARecordFiledForDroppedConnectionsGoes(RepoCase):
    """Repair 0044: a record an older gate filed over a database that dropped connections."""

    def filed(self, report: str, command: tuple[str, ...] = ("pytest",), path: str = "backend") -> None:
        store.write_json(store.tier_a(self.ctx(), evidence.RED_SUITE_FILE),
                         {"command": list(command), "path": path, "tail": "146 errors in 9.1s"})
        kept = store.tier_a(self.ctx(), evidence.REPORTS_DIR, path, witness.KEPT_DIR)
        kept.mkdir(parents=True, exist_ok=True)
        (kept / "report-0.xml").write_text(report)

    def report(self, message: str, errors: int = 2, failure: str = "") -> str:
        """`errors` ERRORs saying `message`, and one FAILURE saying `failure` where it is given."""
        cases = "".join(f'<testcase classname="t" name="test_{n}"><error message="{message}"/>'
                        "</testcase>" for n in range(errors))
        if failure:
            cases += f'<testcase classname="t" name="test_body"><failure message="{failure}"/></testcase>'
        failures = int(bool(failure))
        return (f'<testsuites><testsuite tests="{errors + failures + 1}" errors="{errors}" '
                f'failures="{failures}" skipped="0">{cases}'
                '<testcase classname="t" name="test_ok"/></testsuite></testsuites>')

    def repair(self) -> str:
        return migrate._forget_a_red_suite_of_lost_connections(self.ctx())

    def test_it_is_dropped(self):
        self.filed(self.report("psycopg.OperationalError: server closed the connection "
                               "unexpectedly"))
        self.assertIn("lost its connection", self.repair())
        self.assertIsNone(evidence.red(self.ctx()))

    def test_one_with_any_other_error_stays(self):
        report = self.report("server closed the connection unexpectedly").replace(
            'name="test_1"><error message="server closed the connection unexpectedly"',
            'name="test_1"><error message="KeyError: x"')
        self.filed(report)
        self.assertEqual("", self.repair())
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_one_whose_report_is_gone_stays(self):
        self.filed(self.report("Connection refused"))
        for path in store.tier_a(self.ctx(), evidence.REPORTS_DIR).rglob("*.xml"):
            path.unlink()
        self.assertEqual("", self.repair())
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_one_of_another_command_stays(self):
        self.filed(self.report("Connection refused"), command=("make", "test"))
        self.assertEqual("", self.repair())
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_one_with_a_connection_lost_in_a_tests_body_is_dropped(self):
        """Revision 2 (#339): the first took 54 ERRORs and 1 FAILURE, every one the server's, for
        the code's (#355)."""
        self.filed(self.report("server closed the connection unexpectedly",
                               failure="psycopg.OperationalError: server closed the connection "
                                       "unexpectedly"))
        self.assertIn("lost its connection", self.repair())
        self.assertIsNone(evidence.red(self.ctx()))

    def test_one_with_a_failed_assertion_beside_them_stays(self):
        self.filed(self.report("Connection refused", failure="AssertionError: Connection refused"))
        self.assertEqual("", self.repair())
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_it_is_registered(self):
        self.assertIn("0044-forget-a-red-suite-of-lost-connections", migrate._REPAIRS)

    def test_a_clone_that_had_its_first_revision_has_it_again(self):
        """Which is how a record the first revision left reaches the second."""
        name = "0044-forget-a-red-suite-of-lost-connections"
        store.write_json(store.tier_b(self.ctx(), migrate.LEDGER), {name: {"revision": 1}})
        self.assertIn(name, migrate.pending(self.ctx()))


if __name__ == "__main__":
    unittest.main()
