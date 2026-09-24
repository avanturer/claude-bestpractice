"""Issue #219: the plugin's own bookkeeping was the founder's dirty tree.

Measured, not guessed: 67 modified board files from a dozen branches in one shared checkout,
`git merge --ff-only` refusing over them, the tree lagging the trunk, its suite red on code
nobody present wrote, and the Stop gate refusing a session whose work was already merged.
The three dead-ends of v1.64.0 were symptoms of this one.

Also #220's two cheap halves: a card this session CLOSED is evidence that it was working, and
a red verdict says how far the tree is behind the trunk.
"""

from __future__ import annotations

import unittest

from helpers import RepoCase, git, sid

from claude_bestpractice import evidence, migrate, plan, store, worktree

LEDGER = f"{store.TIER_A_DIRNAME}/plan"


class TestTheLedgerIsKeptOutOfGit(RepoCase):
    def a_committed_card(self):
        task = plan.add(self.ctx(), "a card somebody committed", paths=["src/app.py"],
                        done_when="stated")
        self.commit("commit the ledger, as this repository's founder had")
        return task

    def tracked(self) -> list[str]:
        return [line for line in git(["ls-files", LEDGER], self.repo).splitlines() if line]

    def test_the_exclude_rule_is_written_per_clone(self):
        worktree.hide(self.ctx())
        body = (self.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn("/.claude/claude-bestpractice/plan/", body)
        self.assertIn("/.claude/worktrees/", body, "the older rule was dropped")

    def test_it_is_written_once_and_not_again(self):
        worktree.hide(self.ctx())
        worktree.hide(self.ctx())
        body = (self.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertEqual(1, body.count("/.claude/claude-bestpractice/plan/"))

    def test_a_new_card_is_invisible_to_git_status(self):
        worktree.hide(self.ctx())
        plan.add(self.ctx(), "a card written after the rule", paths=["src/a.py"],
                 done_when="stated")
        self.assertEqual("", git(["status", "--porcelain"], self.repo).strip())

    def test_the_upgrade_takes_the_tracked_ones_out_of_the_index(self):
        task = self.a_committed_card()
        self.assertEqual(1, len(self.tracked()), "precondition: the card is tracked")

        said = migrate._untrack_the_ledger(self.ctx())

        self.assertEqual([], self.tracked())
        self.assertTrue(task.path.is_file(), "the card left the disk")
        self.assertIn("ledger file(s)", said)

    def test_what_it_leaves_behind_is_one_staged_deletion(self):
        """Reversible on purpose: `git restore --staged` puts the index back."""
        self.a_committed_card()
        migrate._untrack_the_ledger(self.ctx())
        staged = git(["diff", "--cached", "--name-status"], self.repo)
        self.assertTrue(staged.startswith("D"), staged)

    def test_the_board_still_reads_the_same(self):
        task = self.a_committed_card()
        migrate._untrack_the_ledger(self.ctx())
        self.assertEqual([task.id], [t.id for t in plan.load_all(self.ctx())])

    def test_a_clone_with_nothing_tracked_is_left_alone(self):
        plan.add(self.ctx(), "never committed", paths=["src/a.py"], done_when="stated")
        self.assertEqual("", migrate._untrack_the_ledger(self.ctx()))
        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo))

    def test_the_founders_own_files_are_not_touched(self):
        """One key, one directory. Everything else in their repository stays theirs."""
        self.write("src/app.py", "x = 1\n")
        self.a_committed_card()
        migrate._untrack_the_ledger(self.ctx())
        self.assertIn("src/app.py", git(["ls-files", "src"], self.repo))

    def test_an_index_git_would_not_write_is_tried_again_next_time(self):
        """An editor or a sibling holding `index.lock` for a moment made `git rm --cached`
        fail, and the step was recorded as done anyway: never run again, and the ledger
        stayed in git in that clone for good."""
        self.a_committed_card()
        store.write_json(store.tier_b(self.ctx(), migrate.LEDGER), {
            name: {"revision": revision} for name, (revision, _) in migrate._REPAIRS.items()
            if name != "0015-untrack-the-ledger"
        })
        busy = self.repo / ".git" / "index.lock"
        busy.write_text("", encoding="utf-8")
        migrate.repair(self.ctx())
        self.assertEqual(["0015-untrack-the-ledger"], migrate.pending(self.ctx()))

        busy.unlink()
        migrate.repair(self.ctx())
        self.assertEqual([], self.tracked())
        self.assertEqual([], migrate.pending(self.ctx()))

    def test_a_clone_where_it_was_recorded_over_a_refusal_gets_it_again(self):
        """Revision 1 recorded itself done whatever git said, so those clones still carry
        their ledger in git; the next revision reaches them."""
        self.a_committed_card()
        store.write_json(store.tier_b(self.ctx(), migrate.LEDGER), {
            name: {"revision": 1 if name == "0015-untrack-the-ledger" else revision}
            for name, (revision, _) in migrate._REPAIRS.items()
        })
        migrate.repair(self.ctx())
        self.assertEqual([], self.tracked())


class TestEverySessionStartKeepsItOut(RepoCase):
    """The upgrade's untrack ran once per clone, in the trees that existed that day, and
    recorded itself done. A tree cut afterwards from a trunk that still tracked the cards had
    them in its index again, and so did a checkout whose index got them back: a transition
    there staged a rename, and the next commit put the ledger back into git."""

    def start(self, cwd=None):
        return self.run_hook("session-start", {
            "session_id": "s1", "hook_event_name": "SessionStart", "source": "startup",
        }, cwd=cwd)

    def tracked(self, tree) -> list[str]:
        return [line for line in git(["ls-files", LEDGER], tree).splitlines() if line]

    def a_committed_card(self):
        plan.add(self.ctx(), "a card somebody committed", paths=["src/app.py"],
                 done_when="stated")
        self.commit("commit the ledger, as this repository's founder had")

    def test_a_card_back_in_the_index_is_taken_out_at_the_next_start(self):
        self.a_committed_card()
        self.start()
        self.assertEqual([], self.tracked(self.repo), "precondition: the upgrade untracked it")
        git(["reset", "-q", "HEAD", "--", LEDGER], self.repo)
        self.assertEqual(1, len(self.tracked(self.repo)), "precondition: it is back")

        said = self.start().stdout

        self.assertEqual([], self.tracked(self.repo))
        self.assertIn("ledger file(s) taken out of git's index", said)

    def test_a_tree_cut_after_the_upgrade_is_cleared_where_a_session_starts_in_it(self):
        self.a_committed_card()
        self.start()
        tree = self.add_worktree("feat-x")
        self.assertEqual(1, len(self.tracked(tree)), "precondition: the new tree tracks it")

        self.start(cwd=tree)

        self.assertEqual([], self.tracked(tree))
        self.assertTrue(any((tree / LEDGER).rglob("*.md")), "the card left the tree's disk")

    def test_a_clone_that_never_committed_it_is_not_touched(self):
        plan.add(self.ctx(), "never committed", paths=["src/a.py"], done_when="stated")
        self.start()
        self.start()
        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo))


class TestTheHealthLineStopsReportingOurOwnRule(RepoCase):
    def test_our_rule_over_the_ledger_is_not_a_hidden_tier_a(self):
        worktree.hide(self.ctx())
        self.assertEqual("", store.hidden_from_git(self.ctx()))

    def test_a_rule_over_the_whole_directory_still_is(self):
        """There it is right: the config the gates read cannot be committed either."""
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{store.TIER_A_DIRNAME}/\n")
        self.assertIn(store.TIER_A_DIRNAME, store.hidden_from_git(self.ctx()))

    def test_the_cards_are_not_counted_as_records_dying_here(self):
        worktree.hide(self.ctx())
        plan.add(self.ctx(), "a card", paths=["src/a.py"], done_when="stated")
        self.assertEqual([], store.ignored_tier_a(self.ctx()))


class TestAClosedCardCountsAsHavingWorked(RepoCase):
    """#220. `done` cleared the owner, so the card carried no trace of who finished it — and
    the gates ask exactly that. Keeping the card open until the session ended was the only
    way to keep them quiet, which is the opposite of what closing means.
    """

    def a_card_done_by(self, session_id: str):
        task = plan.add(self.ctx(), "the work", paths=["src/app.py"], done_when="stated")
        plan.claim(self.ctx(), task.id, session_id, "main")
        return plan.complete(self.ctx(), task.id)[0]

    def test_the_owner_survives_the_closure(self):
        done = self.a_card_done_by("sess-1")
        self.assertEqual("done", done.state)
        self.assertEqual("sess-1", done.owner)

    def test_pause_still_hands_the_work_back(self):
        task = plan.add(self.ctx(), "the work", paths=["src/app.py"], done_when="stated")
        plan.claim(self.ctx(), task.id, "sess-1", "main")
        paused = plan.pause(self.ctx(), task.id, "waiting on the founder")[0]
        self.assertEqual("", paused.owner, "a paused card nobody owns is the point of pausing")

    def test_the_first_write_is_not_refused_after_closing(self):
        """The gate at the first write, driven as the harness drives it."""
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        composed = sid(self.repo, "s1")
        self.a_card_done_by(composed)
        proc = self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / "src" / "app.py"), "content": "x = 2\n"},
        })
        self.assertNotIn("nothing on the board", (proc.stdout or "").lower())

    def a_session_that_closed_its_card(self) -> None:
        """Told what to do by the founder — which is what arms the demand at all — and done."""
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        self.run_hook("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                         "prompt": "add a csv export to src/app.py"})
        self.a_card_done_by(sid(self.repo, "s1"))

    def pre_tool(self, tool: str, tool_input: dict):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": tool, "tool_input": tool_input})

    def test_a_closed_card_covers_its_own_files_and_nothing_else(self):
        """Counted for any path, one closed card was a licence to write anything for the
        rest of the session: the founder's next card was advertised as ready to start while
        this session was already writing it, and no card said so."""
        self.a_session_that_closed_its_card()
        own = self.pre_tool("Write", {"file_path": str(self.repo / "src" / "app.py"),
                                      "content": "x = 2\n"})
        self.assertNotEqual("deny", self.hook_decision(own), self.hook_reason(own))
        other = self.pre_tool("Write", {"file_path": str(self.repo / "src" / "unrelated.py"),
                                        "content": "x = 2\n"})
        self.assertEqual("deny", self.hook_decision(other))
        self.assertIn("working on src/unrelated.py", self.hook_reason(other))

    def test_a_closed_card_does_not_start_work_that_names_no_file(self):
        """`git merge` writes no file, so there is nothing a closed card's files can cover:
        taking a branch in after the card is closed is new work, and it needs a card."""
        self.a_session_that_closed_its_card()
        proc = self.pre_tool("Bash", {"command": "git merge feat/theirs"})
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("nothing on the board says this session is working", self.hook_reason(proc))


class TestATreeBehindTheTrunkSaysSo(RepoCase):
    def test_it_names_the_distance_and_the_command(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("first")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        # The trunk moves on; this tree does not.
        self.write("src/app.py", "x = 2\n")
        self.commit("the trunk moves on")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        git(["reset", "--hard", "-q", "HEAD~1"], self.repo)

        said = evidence.behind_the_trunk(self.ctx())
        self.assertIn("1 commit(s) behind", said)
        self.assertIn("--ff-only", said)

    def test_a_tree_level_with_the_trunk_says_nothing(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("first")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        self.assertEqual("", evidence.behind_the_trunk(self.ctx()))

    def test_no_trunk_at_all_says_nothing(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("first")
        self.assertEqual("", evidence.behind_the_trunk(self.ctx()))


if __name__ == "__main__":
    unittest.main()
