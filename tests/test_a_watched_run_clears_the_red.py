"""Issue #287: a red record that no green run cleared, and a refusal that named none that could.

The Stop gate's own `pytest` in `backend/` ran on the shared database at 5432, met 137 dropped
connections behind Docker's proxy on WSL, and was recorded red. In the same tree the session then
ran `pytest` from `backend/`, `pytest -n auto` and `make test`: 10969 passed each time, exit 0.
`failing-suite.json` did not change, and `gh pr merge` went on refusing with "that same command
passing clears it".

Three faults behind it. Only a run this plugin watches is evidence, and the refusal never said so
or named one. `backend/` was a suite the gate had only detected, so the founder's `test_command`
could not clear its record, as it would have before anything was guessed (decision 0012). And no
watched run could be asked for: a Stop's and a push's were the only ones, and the gate's own run
is not the project's — its `make test` sets up what bare `pytest` does not.
"""

from __future__ import annotations

import subprocess
import sys

from helpers import BIN, RepoCase

from claude_bestpractice import config, evidence, suites, vouch, witness

SIX = "".join(f"def test_{n}():\n    assert True\n\n\n" for n in range(6))
# What the project's own command gives the suite and a bare `pytest` does not: here a variable,
# in #287 the database the tests are run against.
NEEDS_ITS_ENVIRONMENT = (
    "import os\n\n\n"
    + "".join(f"def test_{n}():\n    assert os.environ.get('TEST_DATABASE') == 'isolated'\n\n\n"
              for n in range(6)))
MAKE_TEST = "test:\n\tcd backend && TEST_DATABASE=isolated python3 -m pytest -q\n"


class BackendCase(RepoCase):
    """A repository with a detected `backend/` suite, as the founder's has one."""

    def a_backend(self, tests: str = SIX, makefile: str = MAKE_TEST) -> suites.Suite:
        self.write("backend/pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
        self.write("backend/tests/test_api.py", tests)
        self.write("Makefile", makefile)
        self.commit("a backend")
        backend = [suite for suite in suites.detected(self.repo) if suite.path == "backend/"]
        self.assertTrue(backend, "precondition: backend/ is a suite the gate detects")
        return backend[0]

    def gate_went_red(self, suite, command: tuple = ("pytest",)) -> None:
        """The record the Stop gate writes when its own run of `suite` fails, on this tree."""
        evidence.record_red(self.ctx(), list(command), "137 errors in 41.2s", suite,
                            evidence.tree_hash(self.ctx()), executed=6)

    def verify(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "claude-bp"), "verify"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=300)


class TestTheRefusalNamesTheRunThatClearsIt(BackendCase):
    def test_the_gates_own_run_is_named_and_a_shell_run_is_said_not_to_count(self):
        self.gate_went_red(self.a_backend())
        said = evidence.red_problem(self.ctx())
        self.assertNotIn("that same command passing clears it", said)
        self.assertIn("recorded for `pytest`, the Stop gate's own run", said)
        self.assertIn("a run in a session's own shell does not clear it", said)
        self.assertIn("`claude-bp verify` runs the gate's own `pytest` in backend/ again", said)

    def test_their_command_is_named_where_it_answers_for_the_record(self):
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend())
        self.assertIn("`claude-bp verify` runs `make test`, the test_command in config.json",
                      evidence.red_problem(self.ctx()))

    def test_a_record_of_a_command_is_that_commands_to_clear(self):
        self.configure(test_command=["make", "test"])
        evidence.record_red(self.ctx(), ["make", "check"], "1 failed")
        said = evidence.red_problem(self.ctx())
        self.assertNotIn("the Stop gate's own run", said)
        self.assertIn("`claude-bp verify` runs `make check` again", said)

    def test_the_board_names_the_gates_run_and_its_suite(self):
        self.gate_went_red(self.a_backend())
        said = evidence.red_line(self.ctx())
        self.assertIn("`pytest` in backend/ (the Stop gate's own run) does not pass", said)
        self.assertIn("claude-bp verify", said)

    def test_a_red_finish_says_its_own_run_is_not_watched(self):
        seen = witness.Witnessed(1, 6, 6, "6 errors in 0.1s", "pytest", errors=6)
        said = evidence._judge_witnessed(self.ctx(), seen, None, "").reason
        self.assertIn("A run in your own shell is not watched", said)
        self.assertIn("`claude-bp verify`", said)

    def test_a_remembered_failure_names_the_run_for_a_cause_outside_the_tree(self):
        backend = self.a_backend()
        self.gate_went_red(backend)
        said = evidence._standing_failure(self.ctx(), backend, evidence.tree_hash(self.ctx())).reason
        self.assertNotIn("the answer cannot differ", said)
        self.assertIn("`claude-bp verify`", said)

    def test_a_branch_with_no_green_is_told_the_run_it_can_have_now(self):
        self.assertIn("`claude-bp verify`", evidence.unproven(self.ctx()))

    def test_the_pre_push_hooks_report_names_it_too(self):
        self.gate_went_red(self.a_backend())
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-ci"), "record-green", "make check"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertIn("REMAINS", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("claude-bp verify", proc.stdout)
        self.assertNotIn("only that same command passing", proc.stdout)

    def test_verify_needs_nobodys_permission(self):
        """A refusal names it, and a refusal naming a prompt is the interruption (#116)."""
        self.assertTrue(vouch.own_command(f"{BIN / 'claude-bp'} verify"))


class TestTheirCommandAnswersForASuiteTheGateGuessed(BackendCase):
    """Decision 0012: a guess must not make a record harder to clear than it was. Before
    `backend/` was detected, a record of the gate's run there was the repository's, and their
    `test_command` passing cleared it (decision 0035)."""

    def test_their_test_command_passing_clears_the_gates_record_of_a_detected_suite(self):
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend())
        self.assertTrue(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNone(evidence.red(self.ctx()))

    def test_a_suite_they_declared_is_its_own_commands_to_clear(self):
        """They named `backend/` a command of its own, and that is their word for it."""
        self.configure(test_command=["make", "test"],
                       test_commands={"backend/": "python3 -m pytest -q"})
        self.a_backend()
        declared = suites.Suite("backend/", ("python3", "-m", "pytest", "-q"), True)
        self.gate_went_red(declared)
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))
        self.assertTrue(evidence.clear_red(self.ctx(), list(declared.command), 6, declared))

    def test_a_command_only_detected_is_still_not_their_word(self):
        self.gate_went_red(self.a_backend())
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_record_of_another_command_is_not_theirs_to_answer(self):
        """Their word answers for the gate's own run, never for a different command's record."""
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend(), ("make", "check"))
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_suite_that_has_shed_tests_still_clears_nothing(self):
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend())
        self.write("backend/tests/test_api.py", "def test_0():\n    assert True\n")
        self.commit("drop five tests")
        self.assertFalse(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertIsNotNone(evidence.red(self.ctx()))


class TestVerifyMakesTheRunThatClearsIt(BackendCase):
    def test_their_command_runs_in_the_environment_it_sets_up_and_its_pass_clears_the_record(self):
        """The shape of #287: the gate's bare `pytest` fails where `make test` passes."""
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend(NEEDS_ITS_ENVIRONMENT))
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("ran `make test`, the test_command in config.json", proc.stdout)
        self.assertIn("the red record is cleared", proc.stdout)
        self.assertIsNone(evidence.red(self.ctx()))
        self.assertEqual(["make", "test"], (evidence.last_green(self.ctx()) or {}).get("command"))

    def test_without_their_command_the_gates_own_run_is_made_again(self):
        self.gate_went_red(self.a_backend())
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("ran the gate's own `pytest` in backend/ again", proc.stdout)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_a_failure_remembered_for_this_tree_does_not_answer_a_run_asked_for(self):
        """Decision 0013 would repeat the record on this very tree, within the hour. A run asked
        for is a run, as a passing report already buys one (decision 0034)."""
        backend = self.a_backend()
        self.gate_went_red(backend)
        tree = evidence.tree_hash(self.ctx())
        self.assertIsNotNone(evidence._standing_failure(self.ctx(), backend, tree),
                             "precondition: a Stop would repeat the failure without running it")
        self.assertEqual(0, self.verify().returncode)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_a_run_that_fails_keeps_the_record_and_says_what_stands(self):
        """Without their command the gate's own run is what runs, and it fails as it did."""
        self.gate_went_red(self.a_backend(NEEDS_ITS_ENVIRONMENT))
        proc = self.verify()
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertIn("FAILED", proc.stdout)
        self.assertIn("still on record", proc.stdout)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_pass_over_fewer_tests_than_went_red_clears_nothing(self):
        self.configure(test_command=["make", "test"])
        self.gate_went_red(self.a_backend())
        self.write("backend/tests/test_api.py", "def test_0():\n    assert True\n")
        self.commit("drop five tests")
        proc = self.verify()
        self.assertIn("still on record", proc.stdout, proc.stdout + proc.stderr)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_with_nothing_on_record_the_repositorys_suite_gives_the_branch_its_green(self):
        self.a_backend()
        self.assertIn("no test run has ever been observed", evidence.unproven(self.ctx()))
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("the repository's suite, as the Stop gate runs it", proc.stdout)
        self.assertEqual("", evidence.unproven(self.ctx()))

    def test_a_repository_with_nothing_to_run_is_told_so(self):
        self.assertEqual([], config.founders_test_command(self.ctx()))
        proc = self.verify()
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertIn("nothing was run", proc.stdout)

    def test_a_record_of_a_suite_no_longer_here_has_nothing_to_start(self):
        """Its directory gone, nothing drives a runner there and it has no command of its own:
        said so, rather than an empty command line handed to the shell."""
        evidence.record_red(self.ctx(), ["pytest"], "1 failed",
                            suites.Suite("mobile/", ("pytest",), False))
        proc = self.verify()
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("the gate's own `pytest` in mobile/ again could not be started here",
                      proc.stdout)
        self.assertIsNotNone(evidence.red(self.ctx()))


if __name__ == "__main__":
    import unittest

    unittest.main()
