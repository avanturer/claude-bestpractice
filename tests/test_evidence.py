"""The evidence gate's logic: artifacts, freshness, loops, scope drift."""

from __future__ import annotations

import json
import os
import time
import unittest

from helpers import RepoCase, add_origin, another_clone, git, push_from

from claude_bestpractice import evidence, store
from claude_bestpractice.gitctx import changed_files

JUNIT_PASS = '<?xml version="1.0"?><testsuite name="s" tests="4" failures="0" errors="0"></testsuite>'
JUNIT_FAIL = '<?xml version="1.0"?><testsuite name="s" tests="4" failures="1" errors="0"></testsuite>'
JUNIT_EMPTY = '<?xml version="1.0"?><testsuite name="s" tests="0" failures="0" errors="0"></testsuite>'
JUNIT_NESTED = (
    '<?xml version="1.0"?><testsuites>'
    '<testsuite name="a" tests="2" failures="0" errors="0"></testsuite>'
    '<testsuite name="b" tests="3" failures="1" errors="0"></testsuite>'
    "</testsuites>"
)


class TestArtifactParsing(RepoCase):
    def test_junit_pass(self):
        path = self.write("junit.xml", JUNIT_PASS)
        art = evidence.parse_artifact(path)
        self.assertTrue(art.passed)
        self.assertEqual((art.total, art.failed), (4, 0))

    def test_junit_failure(self):
        art = evidence.parse_artifact(self.write("junit.xml", JUNIT_FAIL))
        self.assertFalse(art.passed)

    def test_zero_tests_is_not_a_pass(self):
        """A suite that ran nothing is the cheapest way to fake green."""
        art = evidence.parse_artifact(self.write("junit.xml", JUNIT_EMPTY))
        self.assertFalse(art.passed)
        self.assertIn("no tests collected", art.detail)

    def test_nested_testsuites_are_aggregated(self):
        art = evidence.parse_artifact(self.write("junit.xml", JUNIT_NESTED))
        self.assertEqual((art.total, art.failed), (5, 1))
        self.assertFalse(art.passed)

    def test_the_failing_cases_name_their_files(self):
        """A `<failure>` holding only its message and traceback has no child elements, and
        an element with none is falsy, so every such case read as passing and a report that
        named its failing files named none. Python 3.12 warns about exactly this test."""
        art = evidence.parse_artifact(self.write("junit.xml", (
            '<?xml version="1.0"?><testsuite name="s" tests="3" failures="1" errors="1">'
            '<testcase name="a" file="tests/test_a.py"><failure message="boom">Traceback'
            "</failure></testcase>"
            '<testcase name="b" file="tests/test_b.py"><error message="oops"/></testcase>'
            '<testcase name="c" file="tests/test_c.py"/>'
            "</testsuite>")))
        self.assertEqual(("tests/test_a.py", "tests/test_b.py"), art.failures)

    def test_pytest_json_report(self):
        path = self.write(
            "pytest-report.json",
            '{"exitcode": 0, "summary": {"total": 7, "passed": 7, "failed": 0}}',
        )
        art = evidence.parse_artifact(path)
        self.assertTrue(art.passed)
        self.assertEqual(art.total, 7)

    def test_pytest_json_nonzero_exit_is_not_a_pass(self):
        path = self.write(
            "pytest-report.json",
            '{"exitcode": 1, "summary": {"total": 7, "passed": 7, "failed": 0}}',
        )
        self.assertFalse(evidence.parse_artifact(path).passed)

    def test_garbage_is_not_evidence(self):
        self.assertIsNone(evidence.parse_artifact(self.write("junit.xml", "not xml at all")))


class TestVerify(RepoCase):
    def changed(self) -> list[str]:
        from claude_bestpractice.gitctx import changed_files

        return changed_files(self.ctx())

    def test_no_changes_needs_no_evidence(self):
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertTrue(verdict.ok)

    def test_changes_without_artifact_are_refused(self):
        self.write("feature.py", "x = 1\n")
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertFalse(verdict.ok)
        self.assertIn("not accepted as evidence", verdict.reason)

    def test_the_hint_matches_the_project_stack(self):
        """A gate that tells a Node project to run pytest is one the agent ignores."""
        self.write("package.json", '{"scripts": {"test": "vitest run"}}')
        self.write("feature.js", "export const x = 1\n")
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertIn("vitest", verdict.reason)
        self.assertNotIn("pytest", verdict.reason)

    def test_a_go_report_counts_the_tests_that_passed(self):
        """go-junit-report reads `go test -v`. Without `-v` a passing test prints nothing for
        it to count, and the report the hint produced said no test had run."""
        self.write("go.mod", "module example.com/x\n")
        self.write("x.go", "package x\n")
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertIn("go test -v ./... 2>&1 | go-junit-report -iocopy -out ", verdict.reason)

    def test_the_hint_degrades_when_the_stack_is_unknown(self):
        self.write("feature.txt", "hello\n")
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertIn("JUnit XML reporter", verdict.reason)

    def test_fresh_passing_artifact_is_accepted(self):
        self.write("feature.py", "x = 1\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        self.assertTrue(evidence.verify(self.ctx(), ["junit.xml"], self.changed()).ok)

    def test_stale_artifact_is_refused(self):
        self.write("junit.xml", JUNIT_PASS)
        old = time.time() - 3600
        os.utime(self.repo / "junit.xml", (old, old))
        self.write("feature.py", "x = 1\n")
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertFalse(verdict.ok)
        self.assertIn("older than", verdict.reason)

    def test_failing_artifact_is_refused(self):
        self.write("feature.py", "x = 1\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_FAIL)
        verdict = evidence.verify(self.ctx(), ["junit.xml"], self.changed())
        self.assertFalse(verdict.ok)
        self.assertIn("3/4 passed", verdict.reason)

    def test_untracked_file_counts_as_a_change(self):
        """An agent that creates a file and never stages it still changed the tree."""
        self.write("brand_new.py", "x = 1\n")
        self.assertFalse(evidence.verify(self.ctx(), ["junit.xml"], self.changed()).ok)


class TestMaterialChanges(unittest.TestCase):
    def test_drops_exempt_paths(self):
        """The plugin's own bookkeeping must not demand a test run to justify itself."""
        out = evidence.material_changes(
            ["src/a.py", ".claude/claude-bestpractice/stage.json", "docs/x.md"],
            [".claude/claude-bestpractice/", "docs/"],
        )
        self.assertEqual(out, ["src/a.py"])

    def test_keeps_everything_when_nothing_is_exempt(self):
        self.assertEqual(evidence.material_changes(["a.py", "b.py"], []), ["a.py", "b.py"])

    def test_prefix_match_does_not_cross_a_name_boundary(self):
        """`docs/` must not exempt `docsite/`."""
        self.assertEqual(evidence.material_changes(["docsite/a.py"], ["docs/"]), ["docsite/a.py"])

    def test_a_test_directory_is_exempt_from_drift_and_never_from_verification(self):
        """The default list exempts `tests/`, `test/`, `spec/` and `__tests__/` so that the test
        this gate demands is not called scope drift. The same list decided what was material,
        so a turn that only added a failing test had nothing to verify and finished silent."""
        exempt = ["docs/", "tests/", "test/", "spec/", "__tests__/", "backend/tests/"]
        tests = ["tests/test_billing.py", "test/fixtures/cart.json", "spec/cart_spec.rb",
                 "__tests__/app.test.js", "backend/tests/conftest.py"]
        self.assertEqual(tests, evidence.material_changes(tests + ["docs/guide.md"], exempt))
        self.assertEqual([], evidence.scope_drift(tests, ["src/billing.py"], exempt))


class TestCleanRerun(RepoCase):
    def test_passes_when_committed_tree_is_good(self):
        self.write("tests_ok.py", "def test_ok():\n    assert True\n")
        self.commit()
        verdict = evidence.clean_rerun(self.ctx(), ["python3", "-c", "import sys; sys.exit(0)"])
        self.assertTrue(verdict.ok, verdict.reason)

    def test_catches_reliance_on_uncommitted_state(self):
        """Green here, red on the committed tree — the whole point of the tier."""
        self.write("needed.txt", "present\n")  # never committed
        verdict = evidence.clean_rerun(
            self.ctx(),
            ["python3", "-c", "import os,sys; sys.exit(0 if os.path.exists('needed.txt') else 1)"],
        )
        self.assertFalse(verdict.ok)
        self.assertIn("FAILS on the committed tree", verdict.reason)

    def test_no_command_is_refused_rather_than_assumed_green(self):
        self.assertFalse(evidence.clean_rerun(self.ctx(), []).ok)

    def test_verification_worktree_is_always_removed(self):
        from helpers import git

        before = git(["worktree", "list"], self.repo).count("\n")
        evidence.clean_rerun(self.ctx(), ["python3", "-c", "pass"])
        after = git(["worktree", "list"], self.repo).count("\n")
        self.assertEqual(before, after)


class TestTheCleanRerunHasOnlyTheStopsTime(RepoCase):
    """The re-run had a fixed 300 seconds on top of everything the Stop had already spent.

    A 310-second suite passed the gate's own run and was then refused on every Stop, ten
    minutes each, with "clean re-run exceeded 300s and was killed" — naming nothing to run —
    and the server its test had started was still running after the gate returned.
    """

    def test_running_out_is_inconclusive_and_names_the_command(self):
        verdict = evidence.clean_rerun(self.ctx(), ["sh", "-c", "sleep 30"], "", time.time() + 1)
        self.assertTrue(verdict.ok, "running out of the Stop's time is not the code failing")
        self.assertTrue(verdict.unverified, verdict.reason)
        self.assertIn("`sh -c sleep 30`", verdict.reason)

    def test_with_nothing_left_it_is_not_started(self):
        verdict = evidence.clean_rerun(self.ctx(), ["sh", "-c", "exit 1"], "", time.time() - 1)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertTrue(verdict.unverified)
        self.assertIn("not started", verdict.reason)

    def test_nothing_it_started_outlives_it(self):
        from helpers import process_gone

        pidfile = self.tmp / "server.pid"
        evidence.clean_rerun(
            self.ctx(), ["sh", "-c", f"sleep 300 & echo $! > '{pidfile}'; sleep 30"], "",
            time.time() + 2)
        self.assertTrue(pidfile.is_file(), "precondition: the suite started its server")
        self.assertTrue(process_gone(int(pidfile.read_text())),
                        "the server the suite started outlived the gate")


# Says how many tests ran, so the gate's first tier is a witnessed green and the Stop goes
# on to the clean re-run rather than finishing unverified before it.
READS_THE_SUBMODULE = [
    "python3", "-c",
    "import json, sys; ok = json.load(open('shared/rates.json'))['vat'] == 20; "
    "print('1 passed' if ok else '1 failed'); sys.exit(0 if ok else 1)",
]


class TestTheCleanRerunHasTheSubmodules(RepoCase):
    """A detached worktree checks a submodule out as an empty directory, so a suite reading a
    committed submodule's file failed there — "passes in your working tree but FAILS on the
    committed tree" — on every finish past prototype, in any repository whose tests use one.
    """

    def setUp(self) -> None:
        super().setUp()
        from helpers import make_repo

        library = make_repo(self.tmp, "fixtures-library", seed=False)
        (library / "rates.json").write_text('{"vat": 20}\n', encoding="utf-8")
        git(["add", "-A"], library)
        git(["commit", "-qm", "rates"], library)
        git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "shared"],
            self.repo)
        self.commit("shared fixtures")

    def test_the_committed_tree_gets_them_from_the_checkouts_here(self):
        config = (self.repo / ".git" / "config").read_text(encoding="utf-8")
        verdict = evidence.clean_rerun(self.ctx(), READS_THE_SUBMODULE)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)
        self.assertEqual(config, (self.repo / ".git" / "config").read_text(encoding="utf-8"),
                         "verifying the repository rewrote its configuration")

    def test_without_the_network_or_the_original(self):
        """From this tree's checkout, never the URL: the library is gone, and still found."""
        import shutil

        shutil.rmtree(self.tmp / "fixtures-library")
        verdict = evidence.clean_rerun(self.ctx(), READS_THE_SUBMODULE)
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertFalse(verdict.unverified, verdict.reason)

    def test_one_it_could_not_be_given_makes_a_failure_inconclusive(self):
        git(["submodule", "deinit", "-q", "-f", "shared"], self.repo)
        verdict = evidence.clean_rerun(self.ctx(), READS_THE_SUBMODULE)
        self.assertTrue(verdict.ok, "a failure beside a missing submodule was blamed on the code")
        self.assertTrue(verdict.unverified)
        self.assertIn("shared", verdict.reason)

    def test_the_stop_gate_past_prototype_accepts_the_finish(self):
        self.configure(require_task=False, manage_pull_requests=False, stage_override="traction",
                       test_command=READS_THE_SUBMODULE)
        self.commit("config")
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                        "source": "startup"})
        self.write("app.py", "X = 2\n")
        self.commit("a change past prototype")
        proc = self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                               "stop_hook_active": False})
        self.assertEqual(0, proc.returncode, proc.stderr)


class TestLoopDetection(unittest.TestCase):
    def test_detects_a_three_gram_repeated_three_times(self):
        sigs = ["Bash:a", "Read:b", "Edit:c"] * 3
        self.assertIsNotNone(evidence.detect_loop(sigs))

    def test_ignores_varied_work(self):
        sigs = [f"Edit:file{i}.py" for i in range(20)]
        self.assertIsNone(evidence.detect_loop(sigs))

    def test_ignores_short_history(self):
        self.assertIsNone(evidence.detect_loop(["Bash:a", "Read:b"]))

    def test_only_the_tail_matters(self):
        """Past thrashing that stopped is not a live loop."""
        sigs = ["Bash:x", "Bash:x", "Bash:x"] * 3 + [f"Edit:f{i}" for i in range(12)]
        self.assertIsNone(evidence.detect_loop(sigs))

    def test_detects_a_tight_single_command_loop(self):
        self.assertIsNotNone(evidence.detect_loop(["Bash:npm test"] * 12))


class TestScopeDrift(unittest.TestCase):
    def test_flags_untouched_by_task(self):
        drift = evidence.scope_drift(
            changed=["src/auth.py", "src/billing.py"], task_paths=["src/auth.py"], exempt=[]
        )
        self.assertEqual(drift, ["src/billing.py"])

    def test_directory_in_task_covers_children(self):
        drift = evidence.scope_drift(
            changed=["src/auth/login.py"], task_paths=["src/auth"], exempt=[]
        )
        self.assertEqual(drift, [])

    def test_exempt_paths_are_never_drift(self):
        drift = evidence.scope_drift(
            changed=["docs/x.md", ".claude/claude-bestpractice/config.json"],
            task_paths=["src/auth.py"],
            exempt=["docs/", ".claude/"],
        )
        self.assertEqual(drift, [])

    def test_empty_task_disables_the_check(self):
        """No captured task is our failure, not the agent's. Do not block on it."""
        self.assertEqual(
            evidence.scope_drift(changed=["a.py", "b.py"], task_paths=[], exempt=[]), []
        )


class TestAGreenRunIsStampedWithItsTree(RepoCase):
    """`record_green` wrote a command, a time and a branch — none of which can tell "green
    now" from "green three edits ago". So the push gate had no choice but to re-run the
    suite it had just watched pass: five minutes a push for an answer that could not
    differ. The stamp is the same content-addressed evidence used everywhere else here.
    """

    def test_the_tree_it_was_green_on_is_recorded(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertTrue(evidence.green_covers_tree(self.ctx()))

    def test_one_edit_is_enough_to_stop_covering_it(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.write("src/app.py", "x = 2\n")
        self.commit("change it")
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

    def test_a_dirty_tree_is_never_covered(self):
        """Uncommitted content is not in the tree at all, so no hash can stand for it."""
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.write("src/app.py", "x = 3  # not committed\n")
        self.assertEqual("", evidence.tree_hash(self.ctx()))
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

    def test_a_record_with_no_stamp_covers_nothing(self):
        """Every green written before this release. Unknown must mean run it again."""
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        path = evidence._green_path(self.ctx())
        record = json.loads(path.read_text())
        record.pop("tree")
        path.write_text(json.dumps(record))
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

    def test_nothing_green_covers_nothing(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

    def test_a_repository_cannot_declare_itself_clean(self):
        """`status.showUntrackedFiles=no` is a real setting in a large repository, and it
        makes a bare `git status --porcelain` answer "clean" over a tree full of untracked
        files. The stamp would then claim to cover a tree that is not the one that was
        tested. Claude Code closed the same blind spot in its own auto-mode check in
        2.1.236; ours was open until it was looked for.
        """
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        git(["config", "--local", "status.showUntrackedFiles", "no"], self.repo)
        self.assertEqual("", git(["status", "--porcelain"], self.repo).strip(),
                         "precondition: git itself must be reporting the tree as clean")

        evidence.record_green(self.ctx(), ["pytest"])
        (self.repo / "not-committed.py").write_text("y = 2\n", encoding="utf-8")

        self.assertEqual("", evidence.tree_hash(self.ctx()))
        self.assertFalse(evidence.green_covers_tree(self.ctx()))

if __name__ == "__main__":
    unittest.main()


class TestAFastForwardIsNotAnEdit(RepoCase):
    """Issue #71. `git pull --ff-only` moved the local trunk past eighteen commits other
    sessions had merged, and all 41 of their files were reported as this session's scope
    drift — with "revert what is out of scope" as the advice, which followed literally
    means rewinding other people's merged work.
    """

    def upstream(self):
        """A real remote, because the fix turns on what came from one."""
        origin = self.tmp / "origin"
        git(["clone", "-q", "--bare", str(self.repo), str(origin)], self.tmp)
        git(["remote", "add", "origin", str(origin)], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        return origin

    def test_commits_pulled_from_upstream_are_not_this_sessions_changes(self):
        baseline = git(["rev-parse", "HEAD"], self.repo)
        origin = self.upstream()

        # Another session's work, landing on the trunk and pulled in.
        other = self.tmp / "other"
        git(["clone", "-q", str(origin), str(other)], self.tmp)
        (other / "somebody-elses.py").write_text("x = 1\n", encoding="utf-8")
        git(["add", "-A"], other)
        git(["-c", "user.email=o@t", "-c", "user.name=o", "commit", "-qm", "their work"], other)
        git(["push", "-q", "origin", "HEAD:main"], other)

        git(["pull", "-q", "--ff-only", "origin", "main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)

        self.assertNotIn(
            "somebody-elses.py", changed_files(self.ctx(), baseline),
            "a fast-forward was counted as this session's edit",
        )

    def test_this_sessions_own_work_is_still_measured(self):
        """The floor rises past upstream, not past the session."""
        baseline = git(["rev-parse", "HEAD"], self.repo)
        self.upstream()
        (self.repo / "mine.py").write_text("y = 2\n", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "my work"], self.repo)

        self.assertIn("mine.py", changed_files(self.ctx(), baseline))

    def test_without_a_remote_nothing_arrives_from_anywhere(self):
        """No upstream means no other session, so the baseline stands unchanged."""
        baseline = git(["rev-parse", "HEAD"], self.repo)
        (self.repo / "mine.py").write_text("y = 2\n", encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "my work"], self.repo)

        self.assertIn("mine.py", changed_files(self.ctx(), baseline))


class TestAnotherPullRequestsBranchIsNotThisSessionsWork(RepoCase):
    """Issue #258. The floor rose past the trunk's commits and nobody else's, so a session
    that fast-forwarded onto another pull request's branch to build on it had every file of
    that branch read as its own: a stub in one of them refused the finish four times as
    "introduced in this turn", and the UNVERIFIED mark that left held up the merge.

    What arrived on a remote branch other than this one's own is somebody else's. What the
    session pushed to its own is still its own (decision 0030).
    """

    STUB = "class PhotoStorage:\n    def exists(self, key):\n        pass\n"

    def setUp(self) -> None:
        super().setUp()
        self.theirs = another_clone(add_origin(self.repo, self.tmp), self.tmp / "theirs")
        push_from(self.theirs, "feat/theirs", "photo_storage.py", self.STUB)

        git(["checkout", "-q", "-b", "feat/mine"], self.repo)
        self.baseline = git(["rev-parse", "HEAD"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)

    def changed(self) -> list[str]:
        return changed_files(self.ctx(), self.baseline)

    def onto_theirs(self) -> None:
        git(["merge", "-q", "--ff-only", "origin/feat/theirs"], self.repo)

    def my_work(self) -> None:
        self.write("mine.py", "y = 2\n")
        self.commit("this session's work")

    def test_a_fast_forward_onto_it_is_not_an_edit(self):
        self.onto_theirs()
        self.assertEqual([], self.changed())

    def test_its_stub_is_not_introduced_in_this_turn(self):
        """The reported shape: the session then edits the file it arrived with."""
        from claude_bestpractice import discipline
        from claude_bestpractice.gitctx import authored_floor

        self.onto_theirs()
        self.write("photo_storage.py", self.STUB + "\n\nVERSION = 2\n")
        floor = authored_floor(self.ctx(), self.baseline)
        self.assertEqual([], discipline.introduced(
            self.ctx(), ["photo_storage.py"], self.baseline, floor))

    def test_a_branch_pruned_meanwhile_costs_nothing(self):
        """A sibling's `git fetch --prune` can delete a remote branch between the listing and
        the walk, and one name git no longer knows failed the whole walk."""
        from unittest import mock

        from claude_bestpractice import gitctx

        self.onto_theirs()
        listed = gitctx._arrived_from(self.ctx())
        with mock.patch.object(gitctx, "_arrived_from",
                               return_value=[*listed, "refs/remotes/origin/pruned-meanwhile"]):
            self.assertEqual([], self.changed())

    def test_work_on_top_of_it_is_still_this_sessions(self):
        self.onto_theirs()
        self.my_work()
        self.assertEqual(["mine.py"], self.changed())

    def test_pushing_its_own_branch_does_not_hand_its_work_away(self):
        """The widening #258 was left open over: every remote branch, this one's own
        included, takes the session's work out of its diff the moment it is pushed."""
        self.onto_theirs()
        self.my_work()
        git(["push", "-q", "-u", "origin", "feat/mine"], self.repo)
        self.assertEqual(["mine.py"], self.changed())

    def test_a_branch_cut_from_it_does_not_make_it_the_sessions_own(self):
        """`checkout -b` from a remote branch makes that branch the upstream, so the upstream
        is not what names a session's own remote branch."""
        git(["checkout", "-q", "-b", "feat/cut", "origin/feat/theirs"], self.repo)
        self.my_work()
        self.assertEqual(["mine.py"], self.changed())

    def test_merging_it_into_the_sessions_work_keeps_only_that_work(self):
        self.my_work()
        git(["merge", "-q", "--no-ff", "--no-edit", "origin/feat/theirs"], self.repo)
        self.assertEqual(["mine.py"], self.changed())

    def theirs_again(self, branch: str, rel: str, base: str = "") -> None:
        """One more commit of somebody else's, pushed to `branch` from `base` (or its tip)."""
        push_from(self.theirs, branch, rel, "z = 3\n", base)
        git(["fetch", "-q", "origin"], self.repo)

    def test_joining_it_twice_steps_over_the_later_join(self):
        """Fast-forwarded onto it, worked, then merged its next commit in: the session's work
        stands on two of their commits, and the floor is the later of them."""
        self.onto_theirs()
        self.my_work()
        self.theirs_again("feat/theirs", "photo_index.py")
        git(["merge", "-q", "--no-ff", "--no-edit", "origin/feat/theirs"], self.repo)
        self.assertEqual(["mine.py"], self.changed())

    def test_two_pull_requests_joined_one_floor_steps_over_the_longer(self):
        """Neither inside the other, so no single floor steps over both: it steps over the
        one with more commits, whichever was merged first, and the other is still counted
        (decision 0030)."""
        self.theirs_again("feat/theirs", "photo_index.py")
        self.theirs_again("feat/other", "other.py", base=self.baseline)
        self.my_work()
        mine = git(["rev-parse", "HEAD"], self.repo)
        for order in (("origin/feat/theirs", "origin/feat/other"),
                      ("origin/feat/other", "origin/feat/theirs")):
            with self.subTest(merged=order):
                git(["reset", "-q", "--hard", mine], self.repo)
                for branch in order:
                    git(["merge", "-q", "--no-ff", "--no-edit", branch], self.repo)
                self.assertEqual(["mine.py", "other.py"], self.changed())

    def test_a_pull_request_cut_before_the_session_began_brings_no_trunk_with_it(self):
        """The floor rises only past where the session started. Their branch forked from an
        older trunk, and stepping over it would hand the session every trunk commit between
        that fork and its start."""
        git(["checkout", "-q", "main"], self.repo)
        self.write("trunk.py", "t = 1\n")
        self.commit("the trunk moves on before this session starts")
        git(["push", "-q", "origin", "main"], self.repo)
        git(["checkout", "-q", "-B", "feat/mine"], self.repo)
        self.baseline = git(["rev-parse", "HEAD"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)

        self.my_work()
        git(["merge", "-q", "--no-ff", "--no-edit", "origin/feat/theirs"], self.repo)
        changed = self.changed()
        self.assertNotIn("trunk.py", changed)
        self.assertIn("mine.py", changed)

    def test_a_local_branch_never_takes_the_work_away(self):
        """`git branch backup` before a risky rebase must not make the session's whole
        history somebody else's."""
        self.my_work()
        git(["branch", "backup"], self.repo)
        self.assertEqual(["mine.py"], self.changed())

    def test_with_no_branch_checked_out_what_it_pushed_is_still_its_own(self):
        """Nothing names a remote copy as a detached session's own, so only the trunk is
        asked: counting every remote branch would take away the work it pushed by name."""
        git(["checkout", "-q", "--detach"], self.repo)
        self.my_work()
        git(["push", "-q", "origin", "HEAD:refs/heads/feat/detached"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        self.assertEqual(["mine.py"], self.changed())


APP_READING_ITS_DATA = (
    "import xml.etree.ElementTree as ET\n\n\n"
    "def total():\n    return int(ET.parse('data/report.xml').getroot().get('total'))\n"
)
TEST_OF_THE_DATA = (
    "import unittest\n\nfrom app import total\n\n\n"
    "class T(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(total(), 3)\n"
)


class TestATrackedFileIsTheTreesOwnContent(RepoCase):
    """A name that looks like a run's leftovers is only leftovers when nobody tracks it.

    `tree_hash` let a changed TRACKED file through whenever its name matched an artifact glob
    or sat under a byproduct directory. `data/report.xml` is both an artifact name and a file
    the code reads: fixed in the working tree over a HEAD that still broke it, the green was
    stamped with HEAD's tree and the push-time skip covered a commit that failed; edited the
    other way, a failure the tree no longer had was re-asserted instead of run (decision 0013
    breached in both directions).
    """

    def setUp(self) -> None:
        super().setUp()
        self.configure(require_task=False, manage_pull_requests=False, test_command=[
            "python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        self.write("app.py", APP_READING_ITS_DATA)
        self.write("data/report.xml", '<report total="3"/>\n')
        self.write("tests/__init__.py", "")
        self.write("tests/test_app.py", TEST_OF_THE_DATA)
        self.commit("an app that reads a tracked data file")
        # The session starts here, so what is committed next is its own work.
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                        "source": "startup"})

    def stop(self):
        return self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                               "stop_hook_active": False})

    def test_a_tracked_file_changed_in_place_is_uncommitted_work(self):
        """An artifact's name, and a file under a byproduct directory — both tracked."""
        self.write("coverage/thresholds.json", '{"lines": 80}\n')
        self.commit("thresholds the build reads")
        for rel, body in (("data/report.xml", '<report total="4"/>\n'),
                          ("coverage/thresholds.json", '{"lines": 90}\n')):
            self.write(rel, body)
            self.assertEqual("", evidence.tree_hash(self.ctx()), rel)
            git(["checkout", "--", rel], self.repo)

    def test_what_a_run_leaves_behind_is_still_not(self):
        """The allowance #206 needed stands: untracked byproducts and artifacts."""
        self.write("junit.xml", "<testsuite tests='1'/>\n")
        self.write("coverage/index.html", "<html></html>\n")
        self.write("__pycache__/app.cpython-311.pyc", "x")
        self.write(".claude/claude-bestpractice/notes.json", "{}\n")
        head = git(["rev-parse", "HEAD^{tree}"], self.repo)
        self.assertEqual(head, evidence.tree_hash(self.ctx()))

    def test_a_fix_only_in_the_working_tree_is_not_stamped_as_heads(self):
        self.write("app.py", APP_READING_ITS_DATA + "\n# refactor\n")
        self.write("data/report.xml", '<report total="4"/>\n')
        self.commit("HEAD breaks the suite")
        self.write("data/report.xml", '<report total="3"/>\n')

        proc = self.stop()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(evidence.green_covers_tree(self.ctx()),
                         "the push would skip a suite that fails on what it pushes")

    def test_a_failure_the_tree_no_longer_has_is_run_not_reasserted(self):
        self.write("app.py", APP_READING_ITS_DATA + "\n# refactor\n")
        self.write("data/report.xml", '<report total="4"/>\n')
        self.commit("HEAD breaks the suite")
        self.assertEqual(2, self.stop().returncode, "precondition: the break is recorded red")

        self.write("data/report.xml", '<report total="3"/>\n')
        proc = self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                               "stop_hook_active": True})
        self.assertEqual(0, proc.returncode, proc.stderr)
