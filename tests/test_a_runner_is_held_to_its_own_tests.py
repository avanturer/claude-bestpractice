"""Issue #290: a red `pytest` record that no pass of pytest could clear, beside a Vitest suite.

The repository held 27 pytest tests in one folder and a large Vitest suite. The Stop gate's own
`pytest` went red on branch A, and the test was fixed. On branch B, its CI green, `+merge` was
refused over that record; `claude-bp verify` there ran nothing and named A. On A it ran pytest:
`pytest passed 27 test(s), but this tree declares 1365`, UNVERIFIED, still on record.

Two faults. The count a run of the gate's own is held to was taken over every language, so the
Vitest tests read as tests pytest had been kept from running, and no pass of pytest could reach
it. And the record a tree keeps of the branch it was on refused the merge of the branch it moved
to, while the run that clears it was the other branch's to make.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

from helpers import BIN, RepoCase, git

from claude_bestpractice import delivery, evidence, witness

PYTHON_TESTS = 6
VITEST_TESTS = 120


def python_tests(count: int) -> str:
    return "".join(f"def test_{n}():\n    assert True\n\n\n" for n in range(count))


def vitest_tests(count: int) -> str:
    return "import { expect, it } from 'vitest'\n\n" + "".join(
        f"it('case {n}', () => {{\n  expect({n}).toBe({n})\n}})\n" for n in range(count))


class MixedCase(RepoCase):
    """A repository whose own command is Vitest's, with a few pytest tests in one folder."""

    def setUp(self) -> None:
        super().setUp()
        self.write("package.json", json.dumps({"scripts": {"test": "vitest run"}}))
        self.write("src/app.test.ts", vitest_tests(VITEST_TESTS))
        self.write("scripts/tests/test_tools.py", python_tests(PYTHON_TESTS))
        self.commit("a Vitest app and its Python scripts")
        git(["checkout", "-q", "-b", "feat/a"], self.repo)
        self.assertEqual("pytest", witness.detect(self.repo),
                         "precondition: the gate drives pytest here, not the Vitest suite")

    def gate_went_red(self, executed: int = PYTHON_TESTS) -> None:
        """The record the Stop gate writes when its own run of the repository's suite fails."""
        evidence.record_red(self.ctx(), ["pytest"], f"1 failed, {executed - 1} passed in 0.1s",
                            None, evidence.tree_hash(self.ctx()), executed=executed)

    def a_record_from_before(self) -> None:
        """The record 2.8.0 wrote for that run: its count taken over every language."""
        path = self.repo / ".claude" / "claude-bestpractice" / evidence.RED_SUITE_FILE
        path.write_text(json.dumps({
            "command": ["pytest"], "path": "", "tree_hash": evidence.tree_hash(self.ctx()),
            "executed": PYTHON_TESTS, "declared": PYTHON_TESTS + VITEST_TESTS,
            "first_seen": time.time() - 3600, "last_seen": time.time() - 3600, "gate": "2.8.0",
            "branch": "feat/a", "tree": str(self.repo), "shared_with": "", "tail": "1 failed",
        }), encoding="utf-8")

    def witnessed(self, executed: int) -> evidence.Verdict:
        seen = witness.Witnessed(0, executed, 0, f"{executed} passed in 0.1s", "pytest")
        return evidence._judge_witnessed(self.ctx(), seen, None, "")

    def verify(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "claude-bp"), "verify"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=300)

    def shed(self, python: int = PYTHON_TESTS, vitest: int = VITEST_TESTS) -> None:
        self.write("scripts/tests/test_tools.py", python_tests(python))
        self.write("src/app.test.ts", vitest_tests(vitest))
        self.commit("the tests as they are now")


class TestAPytestRunIsHeldToTheTestsPytestRuns(MixedCase):
    def test_its_count_is_the_python_tests_alone(self):
        root = self.repo
        self.assertEqual(PYTHON_TESTS + VITEST_TESTS, evidence._declared(self.ctx(), root))
        self.assertEqual(PYTHON_TESTS, evidence._declared(self.ctx(), root, "pytest"))
        self.assertEqual(0, evidence._declared(self.ctx(), root, "go"))

    def test_verify_clears_the_record_its_run_went_red_on(self):
        """The shape of #290, end to end."""
        self.gate_went_red()
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(f"pytest passed {PYTHON_TESTS} test(s), and this tree declares "
                      f"{PYTHON_TESTS} in .py files: the run that went red has passed",
                      proc.stdout)
        self.assertIn("the red record is cleared", proc.stdout)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_a_record_an_older_gate_counted_over_every_language_is_cleared_too(self):
        """The record already on the founder's machine: it holds 2.8.0's count, and no repair
        rewrites it, so it is read as it was counted."""
        self.a_record_from_before()
        self.assertIn("claude-bp verify", evidence.red_problem(self.ctx()),
                      "precondition: the refusal names a run, rather than no run clearing it")
        self.assertEqual(0, self.verify().returncode)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_its_pass_is_still_no_witnessed_pass_of_the_tree(self):
        """The Vitest tests ran nowhere: no green, which a push would skip its own run on."""
        verdict = self.witnessed(PYTHON_TESTS)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertIn(f"{VITEST_TESTS} test(s) in .ts files, which pytest does not run",
                      verdict.reason)
        self.assertNotIn("narrowed", verdict.reason)
        self.assertIsNone(evidence.last_green(self.ctx()))

    def test_a_run_short_of_the_python_tests_is_still_narrowed(self):
        self.gate_went_red()
        verdict = self.witnessed(2)
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertIn(f"but this tree declares {PYTHON_TESTS} in .py files. Something narrowed "
                      "the run", verdict.reason)
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_tree_of_one_language_is_judged_as_before(self):
        self.shed(vitest=0)
        self.gate_went_red()
        verdict = self.witnessed(PYTHON_TESTS)
        self.assertFalse(verdict.unverified, verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()))
        self.assertEqual(["pytest"], (evidence.last_green(self.ctx()) or {}).get("command"))


class TestTheBarIsCountedInTheRunsLanguage(MixedCase):
    def test_a_record_of_the_gates_run_counts_its_runners_tests(self):
        self.gate_went_red()
        entry = evidence.red(self.ctx()) or {}
        self.assertEqual(PYTHON_TESTS, entry.get("declared"))
        self.assertEqual([".py"], entry.get("declared_in"))

    def test_a_record_of_a_command_counts_every_language(self):
        """`npm test` may run anything; nothing says which of the tree's tests it is held to."""
        evidence.record_red(self.ctx(), ["npm", "test"], "1 failed")
        entry = evidence.red(self.ctx()) or {}
        self.assertEqual(PYTHON_TESTS + VITEST_TESTS, entry.get("declared"))
        self.assertNotIn("declared_in", entry)

    def test_a_bar_counted_over_every_language_is_not_carried_into_pythons(self):
        """Carried, the 2.8.0 count would have stood over every pass of pytest for good."""
        self.a_record_from_before()
        self.gate_went_red()
        self.assertEqual(PYTHON_TESTS, (evidence.red(self.ctx()) or {}).get("declared"))
        self.assertTrue(evidence.clear_red(self.ctx(), ["pytest"], PYTHON_TESTS))

    def test_a_deleted_python_test_still_holds_the_record(self):
        self.gate_went_red()
        self.shed(python=PYTHON_TESTS - 1)
        self.assertFalse(evidence.clear_red(self.ctx(), ["pytest"], PYTHON_TESTS - 1))
        self.assertIn(f"declares {PYTHON_TESTS - 1} test(s) in .py files where it declared "
                      f"{PYTHON_TESTS}", evidence.red_problem(self.ctx()))

    def test_vitest_tests_added_do_not_hide_a_python_test_deleted(self):
        """Counted over every language, forty new Vitest tests made up for the deleted one."""
        self.gate_went_red()
        self.shed(python=PYTHON_TESTS - 1, vitest=VITEST_TESTS + 40)
        self.assertFalse(evidence.clear_red(self.ctx(), ["pytest"], PYTHON_TESTS - 1))

    def test_vitest_tests_deleted_do_not_hold_pytests_record(self):
        self.gate_went_red()
        self.shed(vitest=VITEST_TESTS // 2)
        self.assertTrue(evidence.clear_red(self.ctx(), ["pytest"], PYTHON_TESTS))


class TestARecordAnotherBranchLeftInTheTree(MixedCase):
    def on_another_branch(self) -> None:
        """Red on `feat/a`, and the tree then moved to `feat/b` with a commit of its own."""
        self.gate_went_red()
        git(["checkout", "-q", "-b", "feat/b", "main"], self.repo)
        self.write("scripts/tests/test_more.py", "def test_b():\n    assert True\n")
        self.commit("feat/b's own work")

    def test_it_does_not_refuse_this_branchs_pull_request(self):
        self.on_another_branch()
        problems = delivery.ready(self.ctx(), "main")
        self.assertFalse([p for p in problems if "the test suite is red" in p], problems)
        self.assertEqual("feat/a", (evidence.red(self.ctx()) or {}).get("branch"),
                         "the record is feat/a's, and stays for feat/a's run to clear")

    def test_it_still_refuses_its_own_branchs(self):
        self.gate_went_red()
        problems = delivery.ready(self.ctx(), "main")
        self.assertTrue([p for p in problems if "the test suite is red" in p], problems)

    def test_its_bar_is_not_carried_onto_this_branchs_record(self):
        """`feat/a` ran twelve; `feat/b` has six of its own and went red on them."""
        self.gate_went_red(executed=12)
        git(["checkout", "-q", "-b", "feat/b", "main"], self.repo)
        before = time.time()
        self.gate_went_red()
        entry = evidence.red(self.ctx()) or {}
        self.assertEqual(PYTHON_TESTS, entry.get("executed"))
        self.assertGreaterEqual(float(entry.get("first_seen") or 0), before,
                                "feat/b's failure was dated by feat/a's")
        self.assertTrue(evidence.clear_red(self.ctx(), ["pytest"], PYTHON_TESTS))


if __name__ == "__main__":
    import unittest

    unittest.main()
