"""Issue #206: one test command per repository, and a failure rediscovered four times.

A mobile-only branch — nothing under the backend in its diff — was refused four times by
the Stop gate, each refusal a full run of the backend pytest suite, over a test that failed
for the contents of a local database. The jest suite that did cover the change did not
exist as far as the gate was concerned.

Three things are proved here: which suites a diff selects, that a suite nothing touched is
not run, and that a failure already observed on exactly this tree is re-asserted instead of
re-run.
"""

from __future__ import annotations

import json
import time
import unittest

from helpers import RepoCase

from claude_bestpractice import config, evidence, suites

# Exits 0 and says three tests passed, which the count floor accepts against the three
# declarations in the fixture below.
PASSES = ["python3", "-c", "print('3 passed in 0.1s')"]
# Exits 1. Nothing in the plan may reach this unless the plan says it should.
FAILS = ["python3", "-c", "import sys; print('1 failed, 2 passed'); sys.exit(1)"]
# What the shell says when the runner is not installed, which is an environment problem
# rather than a statement about the code.
# A WRAPPER whose runner is absent: `make` or `npm` exists, exits non-zero, and says which
# tool it could not find. A bare missing binary is a different state — the gate reads that as
# "could not run" and falls back — and this is the one that has to be told apart from a
# failing test.
NO_RUNNER = ["python3", "-c", "import sys; print('jest: command not found'); sys.exit(2)"]

MOBILE_TESTS = """it('renders', () => {});
it('navigates', () => {});
it('signs in', () => {});
"""


class SuiteCase(RepoCase):
    def a_mobile_app(self) -> None:
        """A subproject with a runner of its own, and three test declarations in it."""
        self.write("mobile/package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.write("mobile/__tests__/app.test.js", MOBILE_TESTS)
        self.write("mobile/src/screen.js", "export const Screen = () => null\n")

    def cfg(self, **values):
        self.configure(**values)
        return config.load(self.ctx())


class TestWhichSuitesAChangeSelects(SuiteCase):
    def test_a_repository_with_one_command_is_unchanged(self):
        """Every single-project repository. The wide command is the whole plan."""
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]), ["src/app.py"])
        self.assertEqual([("", ("make", "test"))], [(s.path, s.command) for s in plan])

    def test_a_diff_inside_one_subproject_runs_only_that_suite(self):
        """The fix. The backend suite is not evidence about the mobile app."""
        self.a_mobile_app()
        cfg = self.cfg(test_command=["make", "test"], test_commands={"mobile/": "npx jest --ci"})
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        self.assertEqual(["mobile/"], [s.path for s in plan])

    def test_a_diff_that_reaches_past_the_subproject_runs_both(self):
        self.a_mobile_app()
        cfg = self.cfg(test_command=["make", "test"], test_commands={"mobile/": "npx jest"})
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js", "backend/api.py"])
        self.assertEqual(["mobile/", ""], [s.path for s in plan])

    def test_the_most_specific_suite_owns_the_file(self):
        cfg = self.cfg(
            test_command=["make", "test"],
            test_commands={"packages/": "make all", "packages/api/": "make api"},
        )
        plan = suites.for_changes(self.ctx(), cfg, ["packages/api/server.py"])
        self.assertEqual(["packages/api/"], [s.path for s in plan])

    def test_a_prefix_does_not_cross_a_name_boundary(self):
        """`mobile/` must not claim `mobile-web/`."""
        cfg = self.cfg(test_command=["make", "test"], test_commands={"mobile/": "npx jest"})
        plan = suites.for_changes(self.ctx(), cfg, ["mobile-web/app.js"])
        self.assertEqual([""], [s.path for s in plan])

    def test_more_suites_than_the_hook_can_afford_collapses_to_one_run(self):
        """The Stop hook has a fixed budget; four suites each want most of it."""
        cfg = self.cfg(
            test_command=["make", "test"],
            test_commands={f"p{i}/": f"make p{i}" for i in range(5)},
        )
        plan = suites.for_changes(self.ctx(), cfg, [f"p{i}/x.py" for i in range(5)])
        self.assertEqual([""], [s.path for s in plan])

    def test_a_subproject_runner_is_detected_without_being_declared(self):
        """config.json is for correcting a detection, not for having one at all."""
        self.a_mobile_app()
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]),
                                 ["mobile/src/screen.js"])
        self.assertEqual(["mobile/"], [s.path for s in plan])

    def test_detection_looks_two_levels_down(self):
        """`apps/mobile` behind a container directory that has no runner of its own."""
        self.write("apps/mobile/package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.write("apps/mobile/__tests__/a.test.js", MOBILE_TESTS)
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]),
                                 ["apps/mobile/src/x.js"])
        self.assertEqual(["apps/mobile/"], [s.path for s in plan])

    def test_dependencies_are_not_subprojects(self):
        self.write("mobile/node_modules/left-pad/package.json",
                   json.dumps({"scripts": {"test": "jest"}}))
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]),
                                 ["mobile/node_modules/left-pad/index.js"])
        self.assertEqual([""], [s.path for s in plan])

    def test_a_malformed_entry_does_not_take_the_others_down(self):
        cfg = self.cfg(test_command=["make", "test"],
                       test_commands={"mobile/": "npx jest", "backend/": 7})
        self.assertEqual(["mobile/"], [s.path for s in suites.scoped(self.ctx(), cfg)])

    def test_a_repository_with_no_command_at_all_plans_nothing(self):
        """Nothing to run is not something to invent; the artifact path answers instead."""
        self.assertEqual([], suites.for_changes(self.ctx(), self.cfg(test_command=[]), ["a.py"]))


class TestAPlantedSuiteIsNotASuite(SuiteCase):
    """The forgery this selection would otherwise hand out for free.

    The count floor compares what a run reports against what the suite's subtree declares,
    and `testcount.plausible` passes anything when the tree declares nothing. So a
    directory holding a `package.json` with a `test` script and no tests at all would be a
    narrow suite with no floor under it — one file for a session to write itself, and every
    number it reports unfalsifiable. A subtree that declares no tests is therefore not a
    detected suite, and the repository's own command answers for those files.

    `test_commands` needs no such rule: `config.json` is on `PROTECTED_STATE` and no
    session can write it, so a declared suite is the founder's word by construction.
    """

    def a_planted_runner(self) -> None:
        self.write("sneaky/package.json", json.dumps({"scripts": {"test": "echo ok"}}))
        self.write("sneaky/src/thing.js", "export const thing = 1\n")

    def test_a_runner_with_no_tests_under_it_is_not_detected(self):
        self.a_planted_runner()
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]),
                                 ["sneaky/src/thing.js"])
        self.assertEqual([""], [s.path for s in plan], "a planted suite narrowed the run")

    def test_the_wide_suite_still_judges_the_change(self):
        """End to end: the forged narrow command is never reached, so the real one decides."""
        self.a_planted_runner()
        self.write("sneaky/Makefile", "test:\n\t@echo '40 passed'\n")
        cfg = self.cfg(test_command=FAILS)
        self.commit("a planted runner and a real failure")
        plan = suites.for_changes(self.ctx(), cfg, ["sneaky/src/thing.js"])
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["sneaky/src/thing.js"],
                                  cfg.test_command, plan)
        self.assertFalse(verdict.ok, verdict.reason)

    def test_real_declarations_under_it_make_it_a_suite_again(self):
        """The honest shape of the same directory. Writing real tests is the price."""
        self.a_planted_runner()
        self.write("sneaky/__tests__/thing.test.js", MOBILE_TESTS)
        plan = suites.for_changes(self.ctx(), self.cfg(test_command=["make", "test"]),
                                 ["sneaky/src/thing.js"])
        self.assertEqual(["sneaky/"], [s.path for s in plan])


class TestDetectionHasAnOffSwitch(SuiteCase):
    """An inference about somebody's repository layout that they cannot switch off is one
    they have to live with. Off is the stricter direction — the wide command answers for
    everything, as it did before suites existed — which is why it is a founder's switch
    rather than an evidence key.
    """

    def test_off_leaves_only_what_the_founder_declared(self):
        self.a_mobile_app()
        cfg = self.cfg(test_command=["make", "test"], detect_suites=False)
        self.assertEqual([], [s.path for s in suites.scoped(self.ctx(), cfg)])
        self.assertEqual([""], [s.path for s in
                                suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])])

    def test_off_does_not_touch_a_declared_suite(self):
        self.a_mobile_app()
        cfg = self.cfg(test_command=["make", "test"], detect_suites=False,
                       test_commands={"mobile/": "npx jest"})
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        self.assertEqual(["mobile/"], [s.path for s in plan])

    def test_the_switch_is_the_founders_word_not_a_sessions(self):
        """It is settable, unlike `test_command` — and harmless because it only tightens."""
        self.assertNotIn("detect_suites", config.EVIDENCE_KEYS)
        self.assertIn("detect_suites", config.switches_in("detect_suites off"))


class TestOnlyTheSuitesTheDiffTouchesRun(SuiteCase):
    def test_the_wide_suite_is_not_run_for_a_subproject_only_diff(self):
        """#206 end to end: the repository command fails, the change is green anyway."""
        self.a_mobile_app()
        cfg = self.cfg(test_command=FAILS, test_commands={"mobile/": PASSES})
        self.commit("the app and its suite")
        self.write("mobile/src/screen.js", "export const Screen = () => 1\n")

        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["mobile/src/screen.js"],
                                  cfg.test_command, plan)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()), "the wide suite ran and failed")

    def test_a_failing_subproject_suite_still_refuses(self):
        self.a_mobile_app()
        cfg = self.cfg(test_command=PASSES, test_commands={"mobile/": FAILS})
        self.commit("the app and its suite")
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["mobile/src/screen.js"],
                                  cfg.test_command, plan)
        self.assertFalse(verdict.ok)
        self.assertIn("mobile/", verdict.reason)

    def test_a_guessed_runner_that_cannot_start_falls_back_to_the_wide_command(self):
        """Detection is a guess. A guess must not make a finish harder than it was."""
        self.a_mobile_app()
        cfg = self.cfg(test_command=PASSES)
        self.commit("the app")
        plan = [suites.Suite("mobile/", tuple(NO_RUNNER), False)]
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["mobile/src/screen.js"],
                                  cfg.test_command, plan)
        self.assertTrue(verdict.ok, verdict.reason)

    def test_a_declared_runner_that_cannot_start_is_refused(self):
        """The founder said this is how these files are tested. A broken promise blocks."""
        self.a_mobile_app()
        cfg = self.cfg(test_command=PASSES, test_commands={"mobile/": NO_RUNNER})
        self.commit("the app")
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["mobile/src/screen.js"],
                                  cfg.test_command, plan)
        self.assertFalse(verdict.ok)
        self.assertIn("environment problem", verdict.reason)

    def test_a_scoped_suite_is_counted_against_its_own_subtree(self):
        """Against the whole tree, three jest tests look like a run that missed hundreds."""
        self.a_mobile_app()
        for index in range(40):
            self.write(f"tests/test_backend_{index}.py",
                       "".join(f"def test_{index}_{n}():\n    pass\n" for n in range(5)))
        cfg = self.cfg(test_command=FAILS, test_commands={"mobile/": PASSES})
        self.commit("a big backend and a small app")
        plan = suites.for_changes(self.ctx(), cfg, ["mobile/src/screen.js"])
        verdict = evidence.verify(self.ctx(), cfg.artifact_globs, ["mobile/src/screen.js"],
                                  cfg.test_command, plan)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)

    def test_a_scoped_green_does_not_authorise_the_push_time_skip(self):
        """One green jest run must not let a push skip the backend suite for the same tree."""
        self.a_mobile_app()
        self.commit("the app")
        evidence.record_green(self.ctx(), list(PASSES), suites.Suite("mobile/", tuple(PASSES), True))
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

    def test_a_wide_green_still_authorises_it(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("the app")
        evidence.record_green(self.ctx(), ["make", "test"])
        self.assertTrue(evidence.green_covers_tree(self.ctx()))


class TestAFailureIsNotRediscoveredFourTimes(SuiteCase):
    """The suite ran for three and a half minutes to say what it had just said, four times,
    on a tree nobody had touched in between. Remembering a FAILURE is not the result cache
    this module refuses to have: it can only refuse a finish that was already refused.
    """

    def a_recorded_failure(self, path: str = "", age: float = 0.0) -> None:
        suite = None if not path else suites.Suite(path, tuple(PASSES), True)
        evidence.record_red(self.ctx(), list(PASSES), "1 failed, 2 passed",
                            suite, evidence.tree_hash(self.ctx()))
        if age:
            record = json.loads(evidence.store.tier_a(self.ctx(), "failing-suite.json").read_text())
            record["last_seen"] = time.time() - age
            evidence.store.write_json(
                evidence.store.tier_a(self.ctx(), "failing-suite.json"), record
            )

    def verdict(self):
        cfg = self.cfg(test_command=PASSES)
        plan = suites.for_changes(self.ctx(), cfg, ["src/app.py"])
        return evidence.verify(self.ctx(), cfg.artifact_globs, ["src/app.py"],
                               cfg.test_command, plan)

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.write("tests/test_app.py", "def test_a():\n    pass\n" * 1)
        self.commit("the app")

    def test_the_same_tree_gets_the_recorded_failure_back(self):
        self.a_recorded_failure()
        verdict = self.verdict()
        self.assertFalse(verdict.ok, "a command that passes was run instead of the record")
        self.assertIn("not run", verdict.reason)

    def test_an_edit_to_the_tree_runs_it_for_real(self):
        self.a_recorded_failure()
        self.write("src/app.py", "x = 2\n")
        self.commit("change it")
        self.assertTrue(self.verdict().ok, "the record outlived the code it was about")

    def test_uncommitted_work_runs_it_for_real(self):
        """A dirty tree hashes to nothing, so it can never match a record."""
        self.a_recorded_failure()
        self.write("src/app.py", "x = 3  # not committed\n")
        self.assertTrue(self.verdict().ok)

    def test_an_old_record_runs_it_again(self):
        """An environment that has since been fixed is rediscovered within the hour."""
        self.a_recorded_failure(age=evidence.REASSERT_SECONDS + 60)
        self.assertTrue(self.verdict().ok)

    def test_a_record_from_another_suite_is_not_this_suite(self):
        self.a_recorded_failure(path="mobile/")
        self.assertTrue(self.verdict().ok)

    def test_a_record_from_an_older_version_carries_no_tree_and_is_re_run(self):
        self.a_recorded_failure()
        path = evidence.store.tier_a(self.ctx(), "failing-suite.json")
        record = json.loads(path.read_text())
        record.pop("tree_hash")
        evidence.store.write_json(path, record)
        self.assertTrue(self.verdict().ok)


if __name__ == "__main__":
    unittest.main()
