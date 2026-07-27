"""The one number in the evidence gate that the gated party does not author.

Six rounds defeated the gate the same way, one level lower each time: it trusted an
artifact file, then an exit code, then the words "N failed", then the count "N passed".
Every one of those is written to stdout by a process whose command, recipe and source the
agent controls, so reading that stream harder was never going to work — the cheapest
forgery had reached a single shell word.

This counts test declarations out of the test FILES. Moving it means writing real tests.
"""

from __future__ import annotations

import unittest

from helpers import RepoCase

from founder_os import testcount


class TestCounting(RepoCase):
    def test_it_counts_python_declarations(self):
        self.write("tests/test_a.py", "def test_one():\n    pass\n\nasync def test_two():\n    pass\n")
        self.assertEqual(testcount.count_tree(self.repo), 2)

    def test_it_counts_across_languages(self):
        self.write("tests/test_a.py", "def test_x():\n    pass\n")
        self.write("svc/handler_test.go", "func TestA(t *testing.T) {}\nfunc TestB(t *testing.T) {}\n")
        self.write("web/app.test.ts", "it('works', () => {})\ntest('also', () => {})\n")
        self.write("src/lib.rs", "#[test]\nfn a() {}\n")
        self.assertEqual(testcount.count_tree(self.repo), 6)

    def test_a_docstring_mentioning_a_test_is_not_a_test(self):
        """Anchored to a declaration, or prose inflates the number that guards the gate."""
        self.write("tests/test_a.py", '"""call def test_fake() here"""\ndef test_real():\n    pass\n')
        self.assertEqual(testcount.count_tree(self.repo), 1)

    def test_it_ignores_source_that_is_not_a_test_file(self):
        self.write("src/billing.py", "def test_helper():\n    pass\n")
        self.assertEqual(testcount.count_tree(self.repo), 0)

    def test_it_ignores_vendored_trees(self):
        """node_modules holds more tests than any project. Counting them is meaningless."""
        self.write("node_modules/dep/index.test.js", "it('a', ()=>{})\nit('b', ()=>{})\n")
        self.write("tests/test_a.py", "def test_x():\n    pass\n")
        self.assertEqual(testcount.count_tree(self.repo), 1)

    def test_an_empty_repository_counts_zero_rather_than_raising(self):
        self.assertEqual(testcount.count_tree(self.repo), 0)


class TestShortfall(unittest.TestCase):
    def test_a_run_covering_the_tree_has_no_shortfall(self):
        self.assertEqual(testcount.shortfall(40, 40), 0.0)

    def test_parametrised_runs_exceeding_the_count_are_not_suspicious(self):
        """Runners report parametrised cases individually. Over is normal, under is not."""
        self.assertEqual(testcount.shortfall(40, 200), 0.0)

    def test_a_narrowed_run_shows_up(self):
        self.assertGreater(testcount.shortfall(40, 2), 0.9)

    def test_nothing_declared_means_nothing_to_compare(self):
        self.assertEqual(testcount.shortfall(0, 5), 0.0)


class TestItGuardsTheLedger(RepoCase):
    def suite_of(self, n: int) -> None:
        self.write("tests/test_core.py", "".join(f"def test_{i}():\n    assert True\n" for i in range(n)))

    def test_deleting_tests_cannot_clear_a_red_record(self):
        """The single move a blocking Stop gate most incentivises."""
        from founder_os import evidence

        self.suite_of(6)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed in 0.1s")
        self.suite_of(3)
        self.assertFalse(evidence.clear_red(self.ctx(), ["pytest"], 99))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_fabricated_count_is_not_a_witnessed_green(self):
        """`test:\\n\\t@echo '2 passed in 0.03s'` — the cheapest forgery round six found."""
        from founder_os import evidence

        self.suite_of(6)
        verdict = evidence._judge_green_run(self.ctx(), [], ["make", "test"], "2 passed in 0.03s", 0)
        self.assertTrue(verdict.unverified, "a run touching a third of the suite read as green")
        self.assertIn("narrowed run", verdict.reason)

    def test_the_whole_suite_passing_still_clears_it(self):
        from founder_os import evidence

        self.suite_of(6)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed in 0.1s")
        self.assertTrue(evidence.clear_red(self.ctx(), ["pytest"], 6))
        self.assertIsNone(evidence.red(self.ctx()))


if __name__ == "__main__":
    unittest.main()
