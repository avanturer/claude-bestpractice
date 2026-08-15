"""The evidence gate's logic: artifacts, freshness, loops, scope drift."""

from __future__ import annotations

import json
import os
import time
import unittest

from helpers import RepoCase, git

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
