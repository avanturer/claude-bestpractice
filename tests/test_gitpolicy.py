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

    def provisioned(self):
        """The tree the gate made for us. Found by prefix: the name carries a session
        suffix, because two sessions sharing one tree is the failure this all prevents."""
        found = [p for p in self.repo.parent.iterdir()
                 if p.is_dir() and p.name.startswith(f"{self.repo.name}-")]
        return found[0] if len(found) == 1 else None

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

    def test_the_refusal_hands_over_a_worktree_that_already_exists(self):
        """It used to hand over a command, and a command the agent runs is a question.

        Reported from a real session: a chip in the chat asking the founder whether to use
        a worktree. Either the agent stopped to ask, or `git worktree add` needed a
        permission prompt — both are the founder being asked about the plugin's own rule.
        Creating a worktree is not money, legal exposure or product direction, which is the
        list this plugin's own autonomy line says to interrupt them for.
        """
        decision, reason = self.decision()
        self.assertEqual(decision, "deny")

        made = self.provisioned()
        self.assertIsNotNone(made, "the refusal did not create the worktree it names")
        self.assertIn(str(made), reason)
        self.assertIn(f"cd {made}", reason)

    def test_it_says_not_to_ask_the_founder(self):
        """The measured failure was the agent being polite, not the agent being unable."""
        _, reason = self.decision()
        self.assertIn("not a question for the founder", reason)

    def test_a_second_refusal_reuses_the_same_tree(self):
        """Otherwise a session refused twice accumulates worktrees it never asked for."""
        self.decision()
        _, reason = self.decision()
        made = self.provisioned()
        self.assertIn(str(made), reason)
        siblings = [p for p in self.repo.parent.iterdir() if p.name.startswith(f"{self.repo.name}-")]
        self.assertEqual(len(siblings), 1, siblings)

    def test_the_created_worktree_is_outside_the_repository(self):
        """One inside the working tree shows up in every status, glob and scan."""
        self.decision()
        made = self.provisioned()
        self.assertFalse(str(made).startswith(str(self.repo) + "/"))
        self.assertEqual(git(["status", "--porcelain"], self.repo).strip(), "")

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


class TestGitItselfReachesIntoOtherTrees(PolicyCase):
    """`reset --hard` names no file, so every rule keyed on paths saw nothing at all.

    Reported as the incident that made worktree-first a rule in the first place:
    `git -C <other> reset --hard` discards a sibling session's uncommitted work,
    `clean -fd` deletes it outright, and `checkout`/`switch` move a HEAD that session is
    standing on. Nothing appears in a diff, and no lease covers it — a lease is about a
    file somebody is holding, and none of these are about a file.

    Three ways to point git at a tree, all of them explicit, which is the only reason this
    is worth doing statically: `-C <path>`, `--work-tree <path>`, and the directory the
    command runs in, which the `cd` tracking already resolves.
    """

    def bash(self, cwd, command) -> tuple[str, str]:
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": str(cwd),
            },
            cwd=cwd,
        )
        return _verdict(proc)

    def test_every_way_of_naming_another_tree_is_refused(self):
        mine = self.worktree("feat/mine")
        theirs = self.worktree("feat/theirs")
        for command in (
            f"git -C {self.repo} reset --hard HEAD~1",
            f"git -C {self.repo} checkout -b feat/x",
            f"git -C {self.repo} stash",
            f"git -C {self.repo} clean -fd",
            f"cd {self.repo} && git reset --hard",
            f"git --work-tree={self.repo} checkout .",
            f"git -C {theirs} switch main",
            f"git worktree remove {theirs}",
        ):
            decision, reason = self.bash(mine, command)
            self.assertEqual(decision, "deny", f"{command} -> {reason}")

    def test_the_same_commands_are_free_in_our_own_tree(self):
        """A rule that fires on the founder's own work is one they switch off."""
        mine = self.worktree("feat/mine")
        for command in (
            "git reset --hard HEAD~1",
            "git clean -fd",
            f"git -C {mine} stash",
            "git status",
            "git log --oneline -5",
            "git fetch origin",
        ):
            self.assertEqual(self.bash(mine, command)[0], "allow", command)

    def test_it_does_not_block_the_command_that_fixes_a_violation(self):
        """`git switch -c` resolves the trunk rule and `git worktree add` resolves the
        worktree rule. A gate that refuses the fix for its own complaint is a trap, so
        these targets are kept out of the path rules entirely rather than exempted."""
        self.assertEqual(self.bash(self.repo, "git switch -c feat/new")[0], "allow")
        target = self.repo.parent / "wt-brand-new"
        self.assertEqual(
            self.bash(self.repo, f"git worktree add -b feat/brand-new {target}")[0], "allow"
        )

    def test_a_git_command_outside_every_working_tree_is_not_ours_to_judge(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            mine = self.worktree("feat/mine")
            self.assertEqual(
                self.bash(mine, f"cd {tmp} && git init -q r && cd r && git reset --hard")[0],
                "allow",
            )

    def test_an_interpreter_writing_a_literal_path_is_caught(self):
        """Not a general defence and not claimed as one: an interpreter is not statically
        analysable, and anything computed still gets through. This matches the literal
        one-liner form, which is the shape that actually reaches around a path rule."""
        mine = self.worktree("feat/mine")
        target = self.repo / "a.py"
        for command in (
            f"""node -e "require('fs').writeFileSync('{target}','x')" """,
            f"""python3 -c "open('{target}','w').write('x')" """,
        ):
            self.assertEqual(self.bash(mine, command)[0], "deny", command)

    def test_and_the_same_interpreter_write_outside_is_allowed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            mine = self.worktree("feat/mine")
            command = f"""python3 -c "open('{Path(tmp) / 'x.txt'}','w').write('x')" """
            self.assertEqual(self.bash(mine, command)[0], "allow")


class TestWorktreeNamesAndCleanup(PolicyCase):
    """Three findings from a real run, all of them about what provisioning leaves behind."""

    def test_a_slug_is_always_ascii(self):
        """`str.isalnum()` is true for Cyrillic, so a Russian prompt produced a Cyrillic
        directory AND a Cyrillic branch. Git takes both, and then the branch reaches the
        remote on the first push, `git worktree list` prints it octal-escaped, and macOS
        normalises the directory name differently from Linux."""
        from claude_bestpractice import worktree

        for text in ("почини парсер штрихкодов", "🚀🎉", "日本語のみ", "ok fine"):
            self.assertTrue(worktree.slugify(text).isascii(), text)

    def test_cyrillic_is_transliterated_rather_than_dropped(self):
        """A branch called `work` says nothing, and this founder writes Russian prompts."""
        from claude_bestpractice import worktree

        self.assertEqual(
            worktree.slugify("почини парсер штрихкодов"), "pochini-parser-shtrikhkodov"
        )

    def test_a_script_with_no_transliteration_falls_back(self):
        from claude_bestpractice import worktree

        self.assertEqual(worktree.slugify("日本語のみ"), "work")
        self.assertEqual(worktree.slugify(""), "work")

    def test_hostile_input_cannot_escape_the_name(self):
        from claude_bestpractice import worktree

        self.assertEqual(worktree.slugify("../../../../tmp/pwned"), "tmp-pwned")
        self.assertEqual(worktree.slugify(".git/config"), "git-config")
        self.assertEqual(worktree.slugify("--force"), "force")

    def test_two_sessions_never_share_a_tree(self):
        """The last remaining path to the failure this whole subsystem exists to prevent.

        Two sessions with no recorded prompt both slugged to `work`, and two given the same
        instruction both slugged the same — and `provision` returns an existing directory,
        so the second would have been sent into the first one's tree BY THE GATE. Reported
        as a naming nit; it is the silent overwrite, arrived at from the other side.
        """
        from claude_bestpractice import worktree

        self.assertNotEqual(worktree.session_slug("", "A"), worktree.session_slug("", "B"))
        self.assertNotEqual(
            worktree.session_slug("add csv export", "A"),
            worktree.session_slug("add csv export", "B"),
        )

    def test_the_same_session_keeps_its_tree(self):
        from claude_bestpractice import worktree

        self.assertEqual(worktree.session_slug("add csv", "A"), worktree.session_slug("add csv", "A"))

    def test_an_abandoned_empty_tree_is_reaped(self):
        """One per task phrasing, left behind even when the refusal was the only thing that
        ever happened — nine on one repository in a single run. The plugin made them
        unasked, so removing them is the plugin's job too."""
        from claude_bestpractice import worktree

        ctx = self.ctx()
        made = worktree.provision(ctx, "abandoned work", "dead-session")
        self.assertTrue(made.is_dir())

        removed = worktree.reap_unused(ctx, live=set())
        self.assertEqual(removed, [str(made)])
        self.assertFalse(made.is_dir())
        self.assertNotIn("feat/abandoned-work", git(["branch", "--format=%(refname:short)"], self.repo))

    def test_a_tree_holding_work_is_never_reaped(self):
        """Built out of commands that refuse — `git worktree remove` without `--force` and
        `git branch -d` — so the safety is git's, not a condition of ours."""
        from claude_bestpractice import worktree

        ctx = self.ctx()
        made = worktree.provision(ctx, "real work", "dead-session")
        (made / "newfile.py").write_text("y = 2\n", encoding="utf-8")

        self.assertEqual(worktree.reap_unused(ctx, live=set()), [])
        self.assertTrue(made.is_dir())

    def test_a_live_session_keeps_its_tree(self):
        from claude_bestpractice import worktree

        ctx = self.ctx()
        made = worktree.provision(ctx, "in progress", "alive")
        self.assertEqual(worktree.reap_unused(ctx, live={"alive"}), [])
        self.assertTrue(made.is_dir())

    def test_the_sweep_is_announced_on_the_board(self):
        """Deleting directories is the only destructive thing this plugin does on its own
        initiative, and it was the only one it did not report — while it announces every
        worktree it creates. Reported from a real run: six trees became five, silently.

        To someone returning to a tree they had committed in, a directory that is simply
        gone reads as lost work.
        """
        from claude_bestpractice import worktree

        worktree.provision(self.ctx(), "abandoned", "dead-session")
        proc = self.run_hook(
            "session-start",
            {"session_id": "fresh", "hook_event_name": "SessionStart", "cwd": str(self.repo)},
            cwd=self.repo,
        )
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("removed 1 unused worktree", body)
        self.assertIn("branches are kept", body)

    def test_it_says_nothing_when_it_swept_nothing(self):
        """Which is nearly every session, and the context budget is 400 tokens."""
        proc = self.run_hook(
            "session-start",
            {"session_id": "fresh", "hook_event_name": "SessionStart", "cwd": str(self.repo)},
            cwd=self.repo,
        )
        self.assertNotIn(
            "unused worktree", json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        )

    def test_it_never_touches_a_tree_it_did_not_make(self):
        """A worktree the founder created by hand is not this plugin's to remove."""
        from claude_bestpractice import worktree

        theirs = self.worktree("feat/by-hand")
        self.assertEqual(worktree.reap_unused(self.ctx(), live=set()), [])
        self.assertTrue(theirs.is_dir())


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
