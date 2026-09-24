"""The one number in the evidence gate that the gated party does not author.

Six rounds defeated the gate the same way, one level lower each time: it trusted an
artifact file, then an exit code, then the words "N failed", then the count "N passed".
Every one of those is written to stdout by a process whose command, recipe and source the
agent controls, so reading that stream harder was never going to work — the cheapest
forgery had reached a single shell word.

This counts test declarations out of the test FILES. Moving it means writing real tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from unittest import mock

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


class TestTheConfigPinnedIsTheOnePytestReads(RepoCase):
    """`-c` pins the gate's run to a file inside the repository, and it has to be pytest's.

    The first candidate that merely existed was pinned, so a `pyproject.toml` holding only
    `[tool.black]` stood in front of the `setup.cfg` or `tox.ini` that configured pytest:
    `pythonpath = src` was never read, every import failed at collection, and a suite that
    passes for the founder was reported "FAILS … ModuleNotFoundError" and filed red.
    """

    IMPORTS_SRC = "from sample import f\n\n\ndef test_f():\n    assert f() == 1\n"

    def witnessed(self, config: dict, test: str = IMPORTS_SRC):
        """A src layout beside a pyproject that only formats, run by the gate itself."""
        from claude_bestpractice import witness

        self.write("pyproject.toml", "[tool.black]\nline-length = 100\n")
        for name, body in config.items():
            self.write(name, body)
        self.write("src/sample/__init__.py", "def f():\n    return 1\n")
        self.write("tests/test_sample.py", test)
        self.commit("pytest configured beside a pyproject that only formats")
        seen = witness.run(self.ctx())
        self.assertIsNotNone(seen, "pytest was not driven at all")
        return seen

    def test_setup_cfg_is_read_past_a_pyproject_that_only_formats(self):
        seen = self.witnessed({"setup.cfg": "[tool:pytest]\npythonpath = src\n"})
        self.assertTrue(seen.passed, seen.tail)

    def test_tox_ini_is_read_past_a_pyproject_that_only_formats(self):
        seen = self.witnessed({"tox.ini": "[tox]\nenvlist = py\n\n[pytest]\npythonpath = src\n"})
        self.assertTrue(seen.passed, seen.tail)

    def test_the_order_is_pytests_own(self):
        """`.pytest.ini` is pytest's by its name alone, and outranks every shared file."""
        from claude_bestpractice import witness

        self.write("setup.cfg", "[tool:pytest]\n")
        self.write(".pytest.ini", "")
        self.assertEqual(".pytest.ini", witness._pytest_config(self.repo, self.tmp).name)

    def test_with_no_section_anywhere_the_run_still_stands_in_the_repository(self):
        """pytest reads a file without its section as empty, and pinning that file keeps the
        rootdir here. Pinned to an empty file of the gate's own instead, the rootdir moves to
        the gate's temp directory, and every test that finds its data from it breaks."""
        seen = self.witnessed({}, (
            "from pathlib import Path\n\n\ndef test_rootdir(request):\n"
            "    here = Path(__file__).resolve().parent.parent\n"
            "    assert Path(str(request.config.rootpath)).resolve() == here\n"))
        self.assertTrue(seen.passed, seen.tail)


class TestEachProjectRunsUnderItsOwnConfiguration(RepoCase):
    """pytest reads ONE configuration per run, found upward from where it starts, never down.

    The gate started it at the repository root, so a project whose tests live in `backend/`,
    configured by `backend/pyproject.toml`, ran under no configuration at all. Its
    `asyncio_mode = "auto"` was never read, every async fixture errored, and the gate reported
    226 failing over a suite that `make test`, which is `cd backend && pytest`, ran green
    (#230). `pythonpath` stands in for it here: pytest's own, so no plugin is needed to see it.
    """

    def project(self, where: str, value: int = 1, config: str = "pyproject.toml",
                name: str = "sample") -> None:
        """A project under `where` whose tests import from `src/`, which only its own
        configuration puts on the path."""
        self.write(f"{where}{config}", {
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
            "pytest.ini": "[pytest]\npythonpath = src\n",
        }[config])
        self.write(f"{where}src/{name}/__init__.py", f"def f():\n    return {value}\n")
        self.write(f"{where}tests/test_{name}.py",
                   f"from {name} import f\n\n\ndef test_f():\n    assert f() == {value}\n")

    def witnessed(self):
        from claude_bestpractice import witness

        self.commit("tests configured by projects of their own")
        seen = witness.run(self.ctx())
        self.assertIsNotNone(seen, "pytest was not driven at all")
        return seen

    def test_a_project_below_the_root_runs_under_its_own_configuration(self):
        self.project("backend/")
        seen = self.witnessed()
        self.assertTrue(seen.passed, seen.tail)
        self.assertEqual(1, seen.executed)

    def test_a_test_outside_every_project_still_runs(self):
        """The projects are run apart, not instead: what no project configures runs at the root."""
        self.project("backend/")
        self.write("tests/test_root.py", "def test_root():\n    assert 1 == 2\n")
        seen = self.witnessed()
        self.assertEqual((2, 1), (seen.executed, seen.failed), seen.tail)
        self.assertIn("pytest in the repository", seen.tail)

    def test_a_project_inside_a_project_runs_under_its_own(self):
        """The nearest configuration is the one pytest reads when it is handed that test."""
        self.project("services/")
        self.project("services/api/", value=2, config="pytest.ini", name="api")
        seen = self.witnessed()
        self.assertTrue(seen.passed, seen.tail)
        self.assertEqual(2, seen.executed)

    def test_a_failure_in_a_project_is_red_and_says_where_it_ran(self):
        """pytest names each file from where it started, so the refusal says where that was."""
        from claude_bestpractice import evidence

        self.project("backend/", value=1)
        self.project("worker/", name="jobs")
        self.commit("two projects")
        self.write("backend/src/sample/__init__.py", "def f():\n    return 2\n")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"],
                                              ["backend/src/sample/__init__.py"])
        self.assertFalse(verdict.ok)
        self.assertIn("1 failing of 2", verdict.reason)
        self.assertIn("pytest in backend/", verdict.reason)
        self.assertEqual(2, evidence.red(self.ctx())["executed"])

    def test_a_project_the_founder_excluded_is_not_started(self):
        self.configure(witness_exclude=["legacy/"])
        self.project("backend/")
        self.project("legacy/", name="old")
        self.write("legacy/tests/test_broken.py", "def test_broken():\n    assert False\n")
        seen = self.witnessed()
        self.assertTrue(seen.passed, seen.tail)
        self.assertEqual(1, seen.executed)

    def test_an_exclusion_inside_a_project_is_honoured_there(self):
        """`witness_exclude` names paths from the suite's root, and pytest reads a relative
        `--ignore` from where it starts: inside a project that is somewhere else entirely."""
        self.configure(witness_exclude=["backend/tests/test_broken.py"])
        self.project("backend/")
        self.write("backend/tests/test_broken.py", "def test_broken():\n    assert False\n")
        seen = self.witnessed()
        self.assertTrue(seen.passed, seen.tail)
        self.assertEqual(1, seen.executed)

    def test_a_member_without_a_section_is_run_under_the_one_above_it(self):
        """The same defect the other way up. A workspace member's `pyproject.toml` holding only
        `[project]` does not end pytest's search, so `cd packages/api && pytest` reads the
        workspace root's section; the gate pinned the member's file and read nothing."""
        from claude_bestpractice import witness

        self.write("pyproject.toml", '[tool.uv.workspace]\nmembers = ["packages/*"]\n\n'
                                     '[tool.pytest.ini_options]\npythonpath = ["packages/api/src"]\n')
        self.write("packages/api/pyproject.toml", '[project]\nname = "api"\nversion = "0"\n')
        self.write("packages/api/src/sample/__init__.py", "def f():\n    return 1\n")
        self.write("packages/api/tests/test_sample.py",
                   "from sample import f\n\n\ndef test_f():\n    assert f() == 1\n")
        self.commit("a workspace configured once, at its root")
        seen = witness.run(self.ctx(), where=self.ctx().worktree_root / "packages" / "api")
        self.assertTrue(seen.passed, seen.tail)

    def test_nothing_above_the_repository_is_read(self):
        """Up to the repository's root and never past it: a `pytest.ini` beside the clone is in
        no diff anybody reviews, which is why the gate pins a file at all."""
        from claude_bestpractice import witness

        (self.tmp / "pytest.ini").write_text("[pytest]\npythonpath = repo/src\n", encoding="utf-8")
        self.write("src/sample/__init__.py", "def f():\n    return 1\n")
        self.write("tests/test_sample.py", "from sample import f\n\n\ndef test_f():\n"
                                           "    assert f() == 1\n")
        self.commit("a suite that only imports with help from outside the repository")
        seen = witness.run(self.ctx())
        self.assertFalse(seen.passed, "a configuration outside the repository shaped the run")

    def test_one_configuration_is_one_run(self):
        """Every repository without a project of its own below the root runs as it always has."""
        from claude_bestpractice import witness

        self.write("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\n')
        self.write("docs/pyproject.toml", "[tool.pytest.ini_options]\n")
        self.write("src/sample/__init__.py", "def f():\n    return 1\n")
        self.write("tests/unit/test_sample.py", "from sample import f\n\n\ndef test_f():\n"
                                                "    assert f() == 1\n")
        self.assertEqual([self.repo], witness.projects(self.repo),
                         "a configuration with no test under it is not a project to run")
        seen = self.witnessed()
        self.assertTrue(seen.passed, seen.tail)
        self.assertNotIn("pytest in", seen.tail)

    def test_the_projects_share_one_budget(self):
        """The Stop hook's time is one clock. Each run handed all of it could spend it twice
        over, past the point where the harness kills the gate and nobody is told."""
        from claude_bestpractice import witness

        for where in ("api/", "web/"):
            self.project(where, name=where.strip("/"))
            self.write(f"{where}tests/test_slow.py",
                       "import time\n\n\ndef test_slow():\n    time.sleep(2)\n")
        self.commit("two projects that take their time")
        with mock.patch.object(witness, "timeout_for", return_value=3.0):
            with self.assertRaises(witness.RanOutOfTime) as ran_out:
                witness.run(self.ctx())
        self.assertEqual(3.0, ran_out.exception.seconds)


class TestTheRedRecordCountsFromTheGatesOwnReport(RepoCase):
    """The mark a green has to reach was read out of the run's output, and the output of a
    failing run is whatever its tests printed. One `print('9999 passed')` in a failing test
    set it past anything the suite could ever execute, and the record could never clear."""

    def test_what_a_failing_test_prints_is_not_a_count(self):
        from claude_bestpractice import evidence

        self.write("tests/test_noisy.py",
                   "def test_noisy():\n    print('9999 passed')\n    assert False\n")
        self.commit("a failing test that prints a summary of its own")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"],
                                              ["tests/test_noisy.py"])
        self.assertFalse(verdict.ok)
        self.assertIn("9999 passed", verdict.reason, "precondition: the tail carries the print")
        self.assertEqual(1, evidence.red(self.ctx())["executed"])


CALC_GO = """package calc

func Add(a, b int) int { return a + b }
func Sub(a, b int) int { return a - b }
func Mul(a, b int) int { return a * b }
"""

CALC_GO_TESTS = """package calc

import "testing"

func TestAdd(t *testing.T) { if Add(2, 3) != 5 { t.Fatal("add") } }
func TestSub(t *testing.T) { if Sub(5, 3) != 2 { t.Fatal("sub") } }
func TestMul(t *testing.T) { if Mul(2, 3) != 6 { t.Fatal("mul") } }
"""


class TestAGoEnvFileCannotNarrowTheRun(RepoCase):
    """`go env -w GOFLAGS=-run=TestAdd` is one command, outside the repository, in no diff.

    The gate passed `GOFLAGS=""` to its own `go test` to neutralise it, and go reads an
    empty variable as an unset one — so it went back to the very file `go env -w` wrote.
    A regression in `Mul` failed the first Stop and passed the next, and the red record for
    it was cleared on the way.
    """

    def test_the_run_is_never_handed_a_goflags_go_would_ignore(self):
        """Holds wherever the suite runs, with or without a go toolchain installed."""
        from claude_bestpractice import witness

        handed: dict = {}

        def spawn(_ctx, _argv, env, _where=None, _seconds=None):
            handed.update(env or {})
            return None

        with mock.patch.object(witness, "_spawn", spawn):
            witness._run_go(self.ctx(), None)
        self.assertIn("GOFLAGS", handed)
        self.assertTrue(handed["GOFLAGS"].strip(),
                        "an empty GOFLAGS sends go back to the `go env -w` file")

    @unittest.skipUnless(shutil.which("go"), "no go toolchain on this machine")
    def test_a_flag_written_with_go_env_w_does_not_narrow_the_gates_run(self):
        """The attack end to end: the real file, where `go env -w` puts it, and the real gate."""
        from claude_bestpractice import evidence

        self.configure(require_task=False, manage_pull_requests=False)
        self.write("go.mod", "module example.com/calc\n\ngo 1.21\n")
        self.write("calc.go", CALC_GO)
        self.write("calc_test.go", CALC_GO_TESTS)
        self.commit("a go module and its three tests")
        env = {**os.environ, "XDG_CONFIG_HOME": str(self.tmp / "config")}
        env.pop("GOFLAGS", None)
        env.pop("GOENV", None)

        self.write("calc.go", CALC_GO.replace("return a * b", "return a + b"))
        subprocess.run(["go", "env", "-w", "GOFLAGS=-run=TestAdd|TestSub"], cwd=str(self.repo),
                       env=env, check=True, capture_output=True, timeout=120)
        written = (self.tmp / "config" / "go" / "env").read_text(encoding="utf-8")
        self.assertIn("-run=TestAdd", written, "precondition: go env -w wrote its file")

        proc = self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                               "stop_hook_active": False}, env=env)
        self.assertEqual(2, proc.returncode, proc.stderr or proc.stdout)
        self.assertIn("1 failing of 3", proc.stderr)
        self.assertIsNotNone(evidence.red(self.ctx()), "the regression left no red record")
        self.assertIsNone(evidence.last_green(self.ctx()))
