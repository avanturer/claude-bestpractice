"""Three ways a green finish was available without the tests passing.

Each was found by execution, and each defeated the gate's central claim — that completion
is accepted on evidence and never on assertion — while the doctor reported every check
passing and the suite reported OK.
"""

from __future__ import annotations

import json
import time
import subprocess
import sys
import unittest

from helpers import RepoCase, git


class TestTheRunnersOwnWordsOutrankItsExitCode(RepoCase):
    """A Makefile recipe prefixed with `-`, a `|| true`, a wrapper that eats the status.

    All three are ordinary, and all three handed the gate exit 0 over the literal text
    "1 failed". The gate called it green and cleared the red-suite ledger.
    """

    def failing_suite_behind_a_swallowed_status(self) -> None:
        self.write("tests/test_x.py", "def test_broken():\n    assert False\n")
        self.write("Makefile", "test:\n\t-python3 -m pytest -q\n")
        self.commit("suite")
        self.write("src.py", "x = 1\n")

    def test_it_is_refused(self):
        """However the gate gets there, a failing suite must not finish.

        It now gets there by running pytest itself rather than the project's recipe, so
        the swallowed exit status never reaches it — the wrapper is out of the trust path
        entirely. The message below is the direct-run one; the swallowed-status message is
        asserted against the fallback path in `test_the_fallback_still_reads_the_output`.
        """
        self.failing_suite_behind_a_swallowed_status()
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo)},
        )
        self.assertEqual(proc.returncode, 2, "a suite reporting failures finished green")
        self.assertIn("FAILS on the code as it stands", proc.stderr)

    def test_the_fallback_still_reads_the_output(self):
        """When no runner is drivable, the swallowed-status check is what is left.

        "Tests failed" would send the agent to fix passing tests; the status is the lie,
        so the message has to name the status.
        """
        from claude_bestpractice import evidence

        verdict = evidence._judge_green_run(
            self.ctx(), [], ["make", "test"], "1 failed, 3 passed in 0.1s", 0
        )
        self.assertFalse(verdict.ok)
        self.assertIn("swallowing the exit status", verdict.reason)

    def test_a_genuinely_passing_suite_is_still_accepted(self):
        """The check is worthless if it refuses the honest case too."""
        from claude_bestpractice import evidence

        self.assertEqual(evidence._failures_from_output("4 passed in 0.10s"), 0)
        self.assertEqual(evidence._failures_from_output("Ran 4 tests in 0.1s\n\nOK\n"), 0)

    def test_it_reads_unittest_as_well_as_pytest(self):
        from claude_bestpractice import evidence

        self.assertEqual(evidence._failures_from_output("FAILED (failures=2, errors=1)"), 3)
        self.assertEqual(evidence._failures_from_output("2 failed, 3 passed in 1.0s"), 2)


class TestByproductDirectoriesDoNotHideSource(RepoCase):
    """`coverage/` is a report directory in most repositories and a package in some.

    The component match could not tell them apart, so every source file under any
    directory named coverage, htmlcov, .tox or .gradle — at any depth — was invisible to
    the gate, and a red suite in that code was never run at all.
    """

    def test_source_under_a_byproduct_directory_is_visible(self):
        from claude_bestpractice import evidence

        for path in (
            "src/coverage/rules.py",
            "app/htmlcov/view.py",
            "svc/coverage/billing.py",
            "pkg/.tox/helper.py",
            "web/coverage/report.ts",
        ):
            self.assertTrue(
                evidence.material_changes([path], exempt=()),
                f"{path} is invisible to the gate",
            )

    def test_actual_byproducts_are_still_hidden(self):
        """Otherwise the gate reports its own test run as the agent's scope drift."""
        from claude_bestpractice import evidence

        for path in (
            "__pycache__/mod.cpython-311.pyc",
            "htmlcov/index.html",
            "coverage/lcov.info",
            ".pytest_cache/v/cache/lastfailed",
            ".gradle/7.6/checksums.lock",
        ):
            self.assertFalse(
                evidence.material_changes([path], exempt=()),
                f"{path} is reported as a material change",
            )


class TestTheGatedPartyCannotAmendTheRules(RepoCase):
    """`config.json` names the command the Stop gate runs, and lives where the agent writes.

    One Write call — `{"test_command": ["true"]}` — bought a green finish and deleted the
    red-suite ledger recording the real failure. Nothing downstream could tell afterwards.
    """

    def write_event(self, relpath: str) -> dict:
        return {
            "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / relpath), "content": "{}"},
        }

    def decision(self, proc) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def test_the_config_is_refused(self):
        proc = self.run_hook("pre-tool", self.write_event(".claude/claude-bestpractice/config.json"))
        self.assertEqual(self.decision(proc), "deny")

    def test_the_stage_ratchet_is_refused(self):
        proc = self.run_hook(
            "pre-tool", self.write_event(".claude/claude-bestpractice/stage/reached-revenue.json")
        )
        self.assertEqual(self.decision(proc), "deny")

    def test_the_slop_budget_is_refused(self):
        proc = self.run_hook("pre-tool", self.write_event(".claude/claude-bestpractice/slop-budget.json"))
        self.assertEqual(self.decision(proc), "deny")

    def test_the_shell_route_is_refused_too(self):
        """Every rule in this gate has been routed around with a heredoc at least once."""
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
                "tool_name": "Bash",
                "tool_input": {"command": "echo '{}' > .claude/claude-bestpractice/config.json"},
            },
        )
        self.assertEqual(self.decision(proc), "deny")

    def test_the_agents_own_ledgers_are_still_writable(self):
        """The agent is MEANT to write tasks, decisions and dead ends. Over-refusing here
        would break the memory layer to protect the config."""
        for relpath in (
            ".claude/claude-bestpractice/plan/next/0001-task.md",
            ".claude/claude-bestpractice/decisions/0001-x.md",
            ".claude/claude-bestpractice/attempts/0001-y.md",
        ):
            proc = self.run_hook("pre-tool", self.write_event(relpath))
            # Not `is None`. The protected-state refusal above still denies the config;
            # these files are meant to be written, and the gate now says so out loud
            # rather than staying silent (#102).
            self.assertNotEqual("deny", self.decision(proc), f"{relpath} was refused")

    def test_ordinary_source_is_untouched(self):
        proc = self.run_hook("pre-tool", self.write_event("src/app.py"))
        self.assertNotEqual("deny", self.decision(proc))




class TestARedLedgerSurvivesAShrinkingSuite(RepoCase):
    """The record of a failing suite is cleared only by the same suite passing.

    Matching the command's NAME was the first fix and it was not enough: three routes
    kept argv byte-identical while changing what it executed. The identity the check
    compared against is written by the party being gated, so the check compares what the
    run DID — a green that executed fewer tests than the red run is a smaller suite
    passing, and that difference is invisible in argv.
    """

    def red(self, command: list[str], tail: str) -> None:
        from claude_bestpractice import evidence

        evidence.record_red(self.ctx(), command, tail)

    def still_red(self) -> bool:
        from claude_bestpractice import evidence

        return evidence.red(self.ctx()) is not None

    def test_a_narrower_run_behind_the_same_command_does_not_clear_it(self):
        """A Makefile recipe edited to run only the new test. argv never changes."""
        from claude_bestpractice import evidence

        self.red(["make", "test"], "1 failed, 1 passed in 0.1s")
        self.assertFalse(evidence.clear_red(self.ctx(), ["make", "test"], 1))
        self.assertTrue(self.still_red())

    def test_an_honest_wider_failure_cannot_be_used_to_reset_the_bar(self):
        """Route 1: let a wider red overwrite the record, then narrow THAT one.

        Three ordinary turns, no evasion — the second red is genuine. The high-water
        mark is what stops the record's own identity from being laundered.
        """
        from claude_bestpractice import evidence

        self.red(["python3", "-m", "pytest", "-q"], "1 failed, 1 passed in 0.1s")
        self.red(["make", "test"], "1 failed, 1 passed in 0.1s")
        self.assertEqual(evidence.red(self.ctx())["executed"], 2)
        self.assertFalse(evidence.clear_red(self.ctx(), ["make", "test"], 1))
        self.assertTrue(self.still_red())

    def test_deleting_the_failing_test_does_not_clear_it(self):
        """The single most common way an agent turns a blocking Stop gate into a pass."""
        from claude_bestpractice import evidence

        self.red(["go", "test", "./..."], "--- FAIL: TestDiscount\n1 failed")
        verdict = evidence._judge_green_run(
            self.ctx(), [], ["go", "test", "./..."], "?   example.com/svc  [no test files]", 0
        )
        self.assertTrue(verdict.unverified, "a suite that vanished read as verified green")
        self.assertTrue(self.still_red())

    def test_the_same_suite_passing_does_clear_it(self):
        """The check is worthless if the honest case cannot recover."""
        from claude_bestpractice import evidence

        self.red(["python3", "-m", "pytest", "-q"], "1 failed, 1 passed in 0.1s")
        self.assertTrue(evidence.clear_red(self.ctx(), ["python3", "-m", "pytest", "-q"], 2))
        self.assertFalse(self.still_red())


class TestCannotTellIsNotGreen(RepoCase):
    """"The output said nothing I can count" was being reported as "the tests passed"."""

    def test_a_run_with_no_countable_output_is_unverified(self):
        from claude_bestpractice import evidence

        for tail in ("?   example.com/svc  [no test files]", "", "Building...\nDone.\n"):
            verdict = evidence._judge_green_run(self.ctx(), [], ["make", "test"], tail, 0)
            self.assertTrue(verdict.ok, tail)
            self.assertTrue(verdict.unverified, f"{tail!r} counted as witnessed green")

    def test_a_countable_run_is_verified(self):
        from claude_bestpractice import evidence

        verdict = evidence._judge_green_run(self.ctx(), [], ["pytest"], "4 passed in 0.2s", 0)
        self.assertTrue(verdict.ok)
        self.assertFalse(verdict.unverified)

    def test_an_unverified_run_never_writes_a_green_record(self):
        """`claude-bp ship` reads that file and tells the founder "Tests: green"."""
        from claude_bestpractice import evidence

        self.assertIsNone(evidence.last_green(self.ctx()))


if __name__ == "__main__":
    unittest.main()


class TestTheSuiteMustHaveRunThisTree(RepoCase):
    """A passing run of somebody else's copy of your package is not evidence.

    Found on a clone of Flask with a genuine regression in `src/`: the push went out
    green, 491 tests passing, because a `.pth` from an unrelated editable install put a
    different copy of `flask` first on `sys.path`. The gate ran the suite itself and
    observed exit 0 — right about the exit code, wrong about the tree. Forcing the
    worktree onto the path instead produced 24 failures.
    """

    def test_a_package_importing_from_outside_the_worktree_is_caught(self):
        from claude_bestpractice.evidence import _shadowed_package

        # The package lives here...
        self.write("mypkg/__init__.py", "VALUE = 'from the worktree'\n")
        # ...and also somewhere else, earlier on sys.path.
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "mypkg").mkdir(parents=True)
        (elsewhere / "mypkg" / "__init__.py").write_text("VALUE = 'from elsewhere'\n")

        import os

        found = _shadowed_package_with_path(self.repo, os.pathsep.join([str(elsewhere)]))
        self.assertIsNotNone(found, "a shadowed package went undetected")
        self.assertEqual("mypkg", found[0])
        self.assertIn("elsewhere", found[1])

        # And with nothing shadowing it, the same tree is clean.
        self.assertIsNone(_shadowed_package(self.repo))

    def test_a_repository_with_no_packages_at_all_is_not_flagged(self):
        """A Node or Go project must not trip a Python-shaped check."""
        from claude_bestpractice.evidence import _shadowed_package

        self.write("index.js", "module.exports = 1\n")
        self.assertIsNone(_shadowed_package(self.repo))


def _shadowed_package_with_path(root, extra):
    """`_shadowed_package` under a PYTHONPATH that shadows the tree."""
    import os

    from claude_bestpractice.evidence import _shadowed_package

    before = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = extra
    try:
        return _shadowed_package(root)
    finally:
        if before is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = before


class TestTheCeilingCarriesTheReason(RepoCase):
    """The ceiling is how an unverified finish actually happens, and it said nothing.

    Reported as issue #31: the branch wrote the literal string "continuation ceiling
    reached" over the real reason and passed an empty path list — which also skipped the
    attempts record entirely (it is under `if changed:`) and left the open item with no
    subjects, so provenance could never retire it. The warning outlived the code it was
    about. The other ceiling exit, at the end of `main`, always passed both.
    """

    def seed_red(self) -> None:
        self.write("a.py", "def add(a, b):\n    return a - b\n")
        self.write("tests/test_add.py", "from a import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
        self.commit("suite")
        self.write("a.py", "def add(a, b):\n    return a - b - 1\n")

    def hit_the_ceiling(self) -> None:
        """A red suite, then Stop until the escalation ceiling lets the turn end."""
        from claude_bestpractice import evidence

        self.seed_red()
        for _ in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1):
            self.run_hook(
                "evidence-gate",
                {"session_id": "s1", "hook_event_name": "Stop",
                 "stop_hook_active": True, "cwd": str(self.repo)},
            )

    def test_it_names_the_failure_and_the_files(self):
        import json

        from claude_bestpractice import store

        self.hit_the_ceiling()

        marker = store.tier_b(self.ctx(), "unverified.jsonl")
        self.assertTrue(marker.exists(), "no unverified record at all")
        last = json.loads(marker.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertIn("continuation ceiling", last["reason"])
        self.assertIn("suite FAILS", last["reason"], "the real reason was dropped")

    def test_the_open_item_can_be_retired_by_provenance(self):
        import json

        from claude_bestpractice import store

        self.hit_the_ceiling()

        items = store.tier_b(self.ctx(), "open-items.jsonl")
        last = json.loads(items.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertTrue(
            last.get("subject_paths"),
            "no subjects, so this warning can never be retired when the code is rewritten",
        )


class TestAMissingRunnerIsNotACodeFailure(RepoCase):
    """"The suite FAILS on the code as it stands" is a claim about the CODE.

    It was printed verbatim when the suite never ran at all — a bare `pytest` in a Makefile
    that only resolves inside an activated virtualenv, so interactive shells had it and the
    gate's did not. Zero tests executed, zero failures, and a founder sent looking for a
    defect that was not there. Reported as issue #40.
    """

    def test_it_names_the_missing_tool(self):
        from claude_bestpractice import evidence

        tail = "/bin/sh: 1: pytest: not found\nmake: *** [Makefile:21: test] Error 127"
        self.assertIn("pytest", evidence._missing_runner(2, tail))
        self.assertIn("not found on PATH", evidence._missing_runner(2, tail))

    def test_a_bare_127_is_enough(self):
        from claude_bestpractice import evidence

        self.assertIn("not on PATH", evidence._missing_runner(127, "opaque wrapper output"))

    def test_a_genuine_failure_is_still_a_code_failure(self):
        """The whole point is that the two stay distinguishable."""
        from claude_bestpractice import evidence

        self.assertEqual(evidence._missing_runner(1, "1 failed, 3 passed in 0.4s"), "")
        self.assertEqual(evidence._missing_runner(0, "4 passed"), "")

    def test_an_unrunnable_suite_is_not_filed_as_a_red_suite(self):
        """A ledger entry no amount of fixing the code can clear."""
        from claude_bestpractice import evidence

        self.write("Makefile", "test:\n\t@definitely-not-a-real-runner\n")
        self.commit("makefile")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"])
        self.assertIsNotNone(verdict)
        self.assertFalse(verdict.ok)
        self.assertIn("environment problem", verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()), "an unrunnable suite was filed as red")


class TestAGreenRunReportedByTheHookClearsTheRed(RepoCase):
    """`clear_red` was reached from the two paths where this plugin RUNS the suite and
    reads its output, and never from the pre-push hook that reports a run the project made
    itself. So a branch that went red for two minutes stayed red to the merge gate
    forever, whatever passed afterwards.

    As met: a green suite, a gate saying "the test suite is red", and no command that
    changed either — *"whether record-green is broken or whether I am calling it wrong;
    that ambiguity is itself the report"* (#152).
    """

    def red(self, command: list[str], tail: str = "1 failed, 5 passed") -> None:
        from claude_bestpractice import evidence

        evidence.record_red(self.ctx(), command, tail)

    def still_red(self) -> bool:
        from claude_bestpractice import evidence

        return evidence.red(self.ctx()) is not None

    def test_the_same_command_passing_clears_it(self):
        from claude_bestpractice import evidence

        self.red(["make", "test"])
        self.assertTrue(self.still_red(), "precondition: the branch has to be recorded red")

        self.assertTrue(evidence.record_green(self.ctx(), ["make", "test"]))
        self.assertFalse(self.still_red(), "a green suite could not clear its own failure")

    def test_a_different_command_passing_does_not(self):
        """The rule the two runner paths already enforce, and it does not relax here: a
        narrower suite passing says nothing about the one that failed."""
        from claude_bestpractice import evidence

        self.red(["make", "test"])
        self.assertFalse(evidence.record_green(self.ctx(), ["pytest", "tests/test_new.py"]))
        self.assertTrue(self.still_red())

    def test_the_age_of_an_unstamped_record_is_not_invented(self):
        """The first version defaulted a missing stamp to zero and announced "20685d ago",
        which is 1970 wearing the clothes of a measurement."""
        from claude_bestpractice import evidence, store

        self.red(["make", "test"])
        path = store.tier_a(self.ctx(), evidence.RED_SUITE_FILE)
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry.pop("first_seen", None)
        path.write_text(json.dumps(entry), encoding="utf-8")

        said = evidence.red_problem(self.ctx())
        self.assertIn("make test", said, "precondition: the blocker still has to fire")
        self.assertNotIn("ago", said)

    def test_the_age_is_how_long_it_has_been_broken(self):
        """`first_seen`, preserved across re-observations — not when it was last noticed."""
        from claude_bestpractice import evidence, store

        self.red(["make", "test"])
        path = store.tier_a(self.ctx(), evidence.RED_SUITE_FILE)
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["first_seen"] = time.time() - 10 * 86400
        path.write_text(json.dumps(entry), encoding="utf-8")

        self.assertIn("10d ago", evidence.red_problem(self.ctx()))

    def test_the_green_is_still_recorded_either_way(self):
        """Declining to clear the red must not also throw away the green observation."""
        from claude_bestpractice import evidence

        self.red(["make", "test"])
        evidence.record_green(self.ctx(), ["pytest", "-q"])
        self.assertIsNotNone(evidence.last_green(self.ctx()))
