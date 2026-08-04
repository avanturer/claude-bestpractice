"""Worktrees are mandatory and the trunk is not edited — enforced, not requested.

The failure this prevents has no git-level symptom. Two sessions editing one working
tree do not produce a merge conflict; they produce one edit silently replacing another,
with neither session told. So the rule is checked before the write, not after.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from helpers import RepoCase, git


def _verdict(proc) -> tuple[str, str]:
    """(decision, reason) out of a pre-tool response, defaulting to allow."""
    try:
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        return out.get("permissionDecision", "allow"), out.get("permissionDecisionReason", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        return "allow", ""


class PolicyCase(RepoCase):
    relax_git_policy = False

    def ctx(self, root=None):
        from claude_bestpractice.gitctx import resolve

        return resolve(root or self.repo)

    def decision(self, root=None) -> tuple[str, str]:
        target = root or self.repo
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(target / "a.py"), "content": "y = 1\n"},
                "cwd": str(target),
            },
            cwd=target,
        )
        return _verdict(proc)

    def worktree(self, branch: str):
        target = self.repo.parent / f"wt-{branch.replace('/', '-')}"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", branch, str(target)],
            cwd=str(self.repo), capture_output=True, timeout=120, check=True,
        )
        return target


class TestWorktreeIsMandatory(PolicyCase):
    def test_the_main_checkout_is_refused(self):
        decision, reason = self.decision()
        self.assertEqual(decision, "deny")
        self.assertIn("main checkout", reason)

    def test_the_refusal_carries_the_command_that_fixes_it(self):
        """A rule that only says no is one the agent learns to route around."""
        _, reason = self.decision()
        self.assertIn("git worktree add", reason)

    def test_a_worktree_is_allowed(self):
        target = self.worktree("feat/x")
        self.assertEqual(self.decision(target)[0], "allow")

    def test_it_can_be_switched_off_for_a_single_session_repo(self):
        self.configure(require_worktree=False, protect_trunk=False)
        self.assertEqual(self.decision()[0], "allow")


class TestTheRuleIsAboutTheTargetNotTheSession(PolicyCase):
    """The rule held in exactly one direction, and the wrong one was the unsafe one.

    Reported from a real machine with the table filled in. `violations()` asks where the
    SESSION sits — `ctx.is_worktree` — so a session in the main checkout was refused
    correctly, while a session in a worktree could write into the main checkout, or into a
    sibling session's tree, and nothing said a word. That is verbatim the failure the
    refusal text warns about: "one session's edit vanishing under another's, with neither
    told", printed by the gate that was permitting it.

    Leases cover part of the same ground, but only for a file another session is holding
    at that moment. An unheld file went straight through.
    """

    def write_from(self, session_root, target_file) -> tuple[str, str]:
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(target_file), "content": "y = 1\n"},
                "cwd": str(session_root),
            },
            cwd=session_root,
        )
        return _verdict(proc)

    def test_a_worktree_may_not_write_into_the_main_checkout(self):
        mine = self.worktree("feat/mine")
        decision, reason = self.write_from(mine, self.repo / "a.py")
        self.assertEqual(decision, "deny", reason)
        self.assertIn("main checkout", reason)

    def test_a_worktree_may_not_write_into_a_sibling_worktree(self):
        mine = self.worktree("feat/mine")
        theirs = self.worktree("feat/theirs")
        decision, reason = self.write_from(mine, theirs / "a.py")
        self.assertEqual(decision, "deny", reason)
        self.assertIn("worktree", reason)

    def test_a_shell_redirect_into_another_tree_is_refused_too(self):
        """`touched` holds only paths inside OUR tree, so writing elsewhere emptied it and
        the check was skipped entirely — the one case that most needed to reach it."""
        mine = self.worktree("feat/mine")
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": f"printf x > {self.repo / 'a.py'}"},
                "cwd": str(mine),
            },
            cwd=mine,
        )
        self.assertEqual(_verdict(proc)[0], "deny")

    def test_a_worktree_writes_to_its_own_tree_freely(self):
        mine = self.worktree("feat/mine")
        self.assertEqual(self.write_from(mine, mine / "a.py")[0], "allow")

    def test_outside_every_working_tree_there_is_no_rule_to_apply(self):
        """A scratch file in /tmp is not somebody else's work.

        This fired from the main checkout on any Write at all, including into the
        plugin's own scratch directory — so checking the plugin was blocked by the
        plugin. Refusing things that do not matter is how a gate teaches an agent to
        route around it.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "scratch.txt"
            self.assertEqual(self.write_from(self.repo, outside)[0], "allow")
            mine = self.worktree("feat/mine")
            self.assertEqual(self.write_from(mine, outside)[0], "allow")

    def test_a_relative_redirect_is_resolved_where_the_shell_would(self):
        """`cd /tmp/x && printf > a.py` writes /tmp/x/a.py, not <repo>/a.py.

        It was resolved against the repository root regardless, so the command was
        refused as a write to a file it never touched.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_hook(
                "pre-tool",
                {
                    "session_id": "s1",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": f"cd {tmp} && printf x > a.py"},
                    "cwd": str(self.repo),
                },
                cwd=self.repo,
            )
            self.assertEqual(_verdict(proc)[0], "allow")


class TestTheTrunkIsProtected(PolicyCase):
    def test_the_trunk_is_refused_even_inside_a_worktree(self):
        """The worktree rule and the trunk rule are independent failures."""
        self.configure(require_worktree=False)
        decision, reason = self.decision()
        self.assertEqual(decision, "deny")
        self.assertIn("trunk", reason)
        self.assertIn("git switch -c", reason)

    def test_a_feature_branch_is_allowed(self):
        self.configure(require_worktree=False)
        git(["switch", "-qc", "feat/inline"], self.repo)
        self.assertEqual(self.decision()[0], "allow")

    def test_it_can_be_switched_off(self):
        self.configure(require_worktree=False, protect_trunk=False)
        self.assertEqual(self.decision()[0], "allow")


class TestAdoptability(PolicyCase):
    """A rule that makes the plugin impossible to adopt is a rule nobody adopts."""

    def test_a_repository_with_no_commits_is_left_alone(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh"
            fresh.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(fresh), check=True)
            proc = self.run_hook(
                "pre-tool",
                {
                    "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                    "tool_input": {"file_path": str(fresh / "a.py"), "content": "x = 1\n"},
                    "cwd": str(fresh),
                },
                cwd=fresh,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("deny", proc.stdout)

    def test_reads_are_never_blocked(self):
        """Blocking reads would stop the agent learning why it was blocked."""
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": str(self.repo / "a.py")},
            },
        )
        self.assertNotIn("deny", proc.stdout)

    def test_creating_the_worktree_is_not_itself_blocked(self):
        """The command that satisfies the rule must not be caught by the rule."""
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git worktree add -b feat/x ../wt-x"},
            },
        )
        self.assertNotIn("deny", proc.stdout)


class TestTrunkDetection(PolicyCase):
    def test_it_asks_the_repository_rather_than_guessing(self):
        """A default branch not called `main` is ordinary; guessing refuses real work."""
        from claude_bestpractice import gitpolicy

        git(["switch", "-qc", "delivery"], self.repo)
        git(["branch", "-M", "main", "old-main"], self.repo)
        self.assertNotEqual(gitpolicy.default_branch(self.ctx()), "delivery")

    def test_an_unborn_branch_reports_no_history(self):
        """ctx.head used to echo the literal 'HEAD', so every history test read true."""
        import tempfile
        from pathlib import Path

        from claude_bestpractice import gitpolicy
        from claude_bestpractice.gitctx import resolve

        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh"
            fresh.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(fresh), check=True)
            ctx = resolve(fresh)
            self.assertEqual(ctx.head, "")
            self.assertFalse(gitpolicy.has_history(ctx))


if __name__ == "__main__":
    unittest.main()


class TestCommitMessages(PolicyCase):
    """The reader is not a reviewer. It is the next session running `git log`."""

    relax_git_policy = True

    def commit(self, command: str) -> tuple[str, str]:
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": command},
            },
        )
        return _verdict(proc)

    def test_a_message_describing_the_act_of_committing_is_refused(self):
        for message in ("wip", "fix", "update", "stuff", "cleanup"):
            with self.subTest(message=message):
                decision, reason = self.commit(f'git commit -m "{message}"')
                self.assertEqual(decision, "deny")
                self.assertIn("describes committing", reason)

    def test_a_message_that_says_almost_nothing_is_refused_too(self):
        """`.` and `x` are not in the junk list and must not slip through on that."""
        for message in (".", "x", "ok"):
            with self.subTest(message=message):
                self.assertEqual(self.commit(f'git commit -m "{message}"')[0], "deny")

    def test_combined_short_flags_are_parsed(self):
        """`-qm` is ordinary, and a naive `-m` pattern misses exactly the hurried commits."""
        self.assertEqual(self.commit('git commit -qm "wip"')[0], "deny")

    def test_a_conventional_message_is_allowed(self):
        decision, _ = self.commit(
            'git commit -m "feat(billing): charge in minor units to avoid float cents"'
        )
        self.assertEqual(decision, "allow")

    def test_a_prose_message_is_pointed_at_the_convention(self):
        decision, reason = self.commit('git commit -m "made some changes to the thing"')
        self.assertEqual(decision, "deny")
        self.assertIn("conventional commit", reason)

    def test_it_can_be_switched_off(self):
        self.configure(commit_conventions=False)
        self.assertEqual(self.commit('git commit -m "wip"')[0], "allow")

    def test_a_commit_without_m_is_not_second_guessed(self):
        """`git commit` opening an editor carries no message to judge."""
        self.assertEqual(self.commit("git commit")[0], "allow")


class TestConflictMarkers(PolicyCase):
    relax_git_policy = True

    def test_writing_unresolved_markers_is_refused(self):
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.repo / "m.py"),
                    "content": "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> other\n",
                },
            },
        )
        self.assertIn("conflict marker", proc.stdout)

    def test_ordinary_content_with_equals_signs_is_fine(self):
        from claude_bestpractice import gitpolicy

        self.assertEqual(gitpolicy.conflict_complaint("x = 1\nsep = '=' * 40\n"), "")
