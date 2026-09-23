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

from claude_bestpractice import testcount


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
        from claude_bestpractice import evidence

        self.suite_of(6)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed in 0.1s")
        self.suite_of(3)
        self.assertFalse(evidence.clear_red(self.ctx(), ["pytest"], 99))
        self.assertIsNotNone(evidence.red(self.ctx()))

    def test_a_fabricated_count_is_not_a_witnessed_green(self):
        """`test:\\n\\t@echo '2 passed in 0.03s'` — the cheapest forgery round six found."""
        from claude_bestpractice import evidence

        self.suite_of(6)
        verdict = evidence._judge_green_run(self.ctx(), [], ["make", "test"], "2 passed in 0.03s", 0)
        self.assertTrue(verdict.unverified, "a run touching a third of the suite read as green")
        self.assertIn("do not match closely enough", verdict.reason)

    def test_inflating_the_fabricated_count_does_not_help(self):
        """`2` -> `9999`. One keystroke defeated the first version of this check.

        The bound was one-sided — it caught a run reporting FEWER tests than the tree
        declares and blessed any number above as parametrisation. `declared` is counted
        here from the files and `executed` is parsed from the gated party's stdout, so
        the unchecked side was an open door.
        """
        from claude_bestpractice import evidence

        self.suite_of(6)
        for claimed in ("9999 passed in 0.01s", "999999999 passed in 0.01s"):
            verdict = evidence._judge_green_run(self.ctx(), [], ["make", "test"], claimed, 0)
            self.assertTrue(verdict.unverified, f"{claimed!r} read as a witnessed green")

    def test_parametrisation_is_still_allowed_to_exceed_the_count(self):
        """One parametrize over twenty inputs is twenty reported tests from one line."""
        from claude_bestpractice import evidence

        self.suite_of(6)
        verdict = evidence._judge_green_run(self.ctx(), [], ["pytest"], "120 passed in 2.0s", 0)
        self.assertFalse(verdict.unverified)

    def test_the_whole_suite_passing_still_clears_it(self):
        from claude_bestpractice import evidence

        self.suite_of(6)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed, 5 passed in 0.1s")
        self.assertTrue(evidence.clear_red(self.ctx(), ["pytest"], 6))
        self.assertIsNone(evidence.red(self.ctx()))


if __name__ == "__main__":
    unittest.main()


class TestTheGateDrivesTheRunnerItself(RepoCase):
    """The wrapper is out of the trust path, which is the only move that ends the pattern.

    Seven rounds broke this gate by forging whatever number it read, because every number
    it read came from a process the agent's recipe defined. `make test` is now not run at
    all when a real runner is drivable: the gate invokes pytest with a report path of its
    own choosing in a temp directory outside the repository.
    """

    def project(self, regression: bool, recipe: str) -> None:
        self.write("src/__init__.py", "")
        self.write("src/calc.py", "def div(a, b):\n    return a * b\n" if regression
                   else "def div(a, b):\n    return a / b\n")
        body = "from src.calc import div\n\ndef test_div():\n    assert div(9, 3) == 3\n"
        body += "".join(f"def test_pad_{i}():\n    assert True\n" for i in range(10))
        self.write("tests/test_calc.py", body)
        self.write("Makefile", f"test:\n\t{recipe}\n")
        self.commit("project")

    def test_a_forged_recipe_does_not_hide_a_regression(self):
        """Round seven's winning attack: echo a believable count. The recipe is not run."""
        from claude_bestpractice import evidence

        self.project(regression=True, recipe="@echo '11 passed in 0.01s'")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"], [])
        self.assertIsNotNone(verdict)
        self.assertFalse(verdict.ok, "a forged recipe still bought a green")
        self.assertIn("run by the gate itself", verdict.reason)
        self.assertIsNotNone(evidence.red(self.ctx()))
        self.assertIsNone(evidence.last_green(self.ctx()))

    def test_honest_work_passes_even_with_a_nonsense_recipe(self):
        """The recipe is irrelevant in both directions, or this would block correct work."""
        from claude_bestpractice import evidence

        self.project(regression=False, recipe="@echo 'lol nothing here'")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"], [])
        self.assertIsNotNone(verdict)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertIsNotNone(evidence.last_green(self.ctx()))

    def test_it_declines_rather_than_guessing_when_no_runner_is_drivable(self):
        """None means "could not witness", and the caller must not read it as a pass."""
        from claude_bestpractice import witness

        self.write("main.c", "int main(void) { return 0; }\n")
        self.commit("c project")
        self.assertEqual(witness.detect(self.repo), "")
        self.assertIsNone(witness.run(self.ctx()))


class TestItCountsOnlyThisTree(RepoCase):
    """The floor is only a floor while it is this tree's number and nobody else's."""

    def test_sibling_trees_inside_the_checkout_are_not_its_tests(self):
        """The trees this plugin provisions live under `.claude/worktrees/` IN the checkout.

        Counted from there, every sibling's copy of the suite became the main checkout's
        own, and a run of its real suite read as a narrowed one.
        """
        self.write("tests/test_a.py", "def test_x():\n    pass\n")
        self.write(".claude/worktrees/feat/tests/test_a.py", "def test_x():\n    pass\n\ndef test_y():\n    pass\n")
        self.write("wt-by-hand/.git", "gitdir: /elsewhere\n")
        self.write("wt-by-hand/tests/test_a.py", "def test_x():\n    pass\n")
        self.assertEqual(testcount.count_tree(self.repo), 1)

    def test_a_repository_under_a_directory_named_like_a_build_output_still_counts(self):
        """`build` and `vendor` are judged inside the tree, never in the path leading to it."""
        from helpers import make_repo

        nested = make_repo(self.tmp / "build", "project")
        (nested / "tests").mkdir()
        (nested / "tests" / "test_a.py").write_text("def test_x():\n    pass\n")
        self.assertEqual(testcount.count_tree(nested), 1)

    def test_declares_answers_for_one_language(self):
        self.write("test/a.test.js", "test('a', () => {})\n")
        self.assertTrue(testcount.declares(self.repo, ".js"))
        self.assertFalse(testcount.declares(self.repo, ".py"))


class TestPytestIsDrivenOnlyWherePythonIsTested(RepoCase):
    """A `test/` directory is where half the ecosystems keep their suites.

    Found by running a real session on a Node project: pytest was importable, the gate drove
    it over `test/*.test.js`, collected nothing, and refused the finish as "the suite FAILS —
    0 failing of 0" until the session ran out of turns.
    """

    def test_a_node_suite_is_not_pytests(self):
        from claude_bestpractice import witness

        self.write("package.json", '{"scripts": {"test": "node --test"}}\n')
        self.write("test/a.test.js", "import { test } from 'node:test';\ntest('a', () => {});\n")
        self.assertFalse(witness._has_python_tests(self.repo))

    def test_a_cargo_or_go_layout_is_not_pytests(self):
        from claude_bestpractice import witness

        self.write("Cargo.toml", "[package]\nname = \"x\"\n")
        self.write("tests/integration.rs", "#[test]\nfn works() {}\n")
        self.write("go.mod", "module example.com/x\n")
        self.write("test/e2e_test.go", "package e2e\n")
        self.assertFalse(witness._has_python_tests(self.repo))

    def test_a_pyproject_that_only_configures_a_formatter_is_not_pytests(self):
        from claude_bestpractice import witness

        self.write("pyproject.toml", "[tool.black]\nline-length = 100\n")
        self.assertFalse(witness._has_python_tests(self.repo))

    def test_python_tests_or_pytest_configuration_still_are(self):
        from claude_bestpractice import witness

        self.write("tests/test_a.py", "def test_x():\n    pass\n")
        self.assertTrue(witness._has_python_tests(self.repo))
        for name, body in (("pytest.ini", "[pytest]\n"),
                           ("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['t']\n"),
                           ("setup.cfg", "[tool:pytest]\n"), ("tox.ini", "[pytest]\n")):
            bare = self.tmp / f"only-{name}"
            bare.mkdir()
            (bare / name).write_text(body)
            self.assertTrue(witness._has_python_tests(bare), name)

    def test_pytest_collecting_nothing_is_not_a_red_suite(self):
        """Exit status 5 observed nothing about the code, so it must not be filed as red."""
        from claude_bestpractice import evidence, witness

        seen = witness.Witnessed(witness.PYTEST_NO_TESTS, 0, 0, "no tests ran in 0.01s", "pytest")
        verdict = evidence._judge_witnessed(self.ctx(), seen)
        self.assertFalse(verdict.ok)
        self.assertIn("executed NOTHING", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()))

    def test_a_failing_run_is_still_red(self):
        from claude_bestpractice import evidence, witness

        seen = witness.Witnessed(1, 3, 1, "1 failed, 2 passed in 0.1s", "pytest")
        verdict = evidence._judge_witnessed(self.ctx(), seen)
        self.assertFalse(verdict.ok)
        self.assertIn("FAILS", verdict.reason)
        self.assertIsNotNone(evidence.red(self.ctx()))
