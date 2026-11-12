"""A session goes back into its tree without the founder being asked (the founder's report, 2.14.3).

> The session after the WSL crash is still bound to the main checkout, so the gate blocks it;
> it is returning to its worktree — and asks me whether it may enter it. It has to enter
> without permission, always.

Claude Code's worktree docs: "a `path` outside `.claude/worktrees/` prompts for your approval
before entering", and "an `EnterWorktree` permission rule or choosing 'don't ask again' doesn't
suppress this prompt; only `bypassPermissions` mode skips it." A tree made beside the repository
asked the founder on every way back in, and the plugin's advice named the allow rule that does
nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from helpers import BIN, RepoCase, git, session_record_for, sid

from claude_bestpractice import plan, sessions
from claude_bestpractice.gitctx import resolve

ANOTHER_PROCESS = 1


class HandTreeCase(RepoCase):
    """A chat working in a tree made by hand beside the repository, on a card of its own."""

    relax_git_policy = False

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "def parse(text):\n    return text.split(',')\n")
        self.commit("a history to branch from")
        self.hand = self.tmp / "proj-topic"
        git(["worktree", "add", "-q", "-b", "feat/proj-topic", str(self.hand)], self.repo)
        self.home = resolve(self.repo).worktree_root / ".claude" / "worktrees" / "proj-topic"

    def hook(self, gate: str, cwd, session: str = "S", **event):
        return self.run_hook(gate, {"session_id": session, **event}, cwd=cwd)

    def cli(self, cwd, *args: str):
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "S"}
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(cwd), env=env, timeout=120)

    def write_in(self, tree, relpath: str = "src/app.py"):
        return self.hook("pre-tool", tree, hook_event_name="PreToolUse", tool_name="Write",
                         tool_input={"file_path": str(tree / relpath), "content": "x = 1\n"})

    def enter(self, path, cwd=None, **extra):
        return self.hook("pre-tool", cwd or self.repo, hook_event_name="PreToolUse",
                         tool_name="EnterWorktree", tool_input={"path": str(path)}, **extra)

    def pin(self, tree, pid: int, raw_id: str = "S") -> None:
        ctx = resolve(tree)
        identity = sid(tree, raw_id)
        record = sessions.get(ctx, identity) or session_record_for(ctx, identity, pid)
        record.pid, record.pid_trust = pid, sessions.PID_TRUST_OWNER
        sessions.register(ctx, record)

    def gone(self) -> int:
        ended = subprocess.Popen([sys.executable, "-c", ""])
        ended.wait()
        return ended.pid

    def working_in_the_hand_tree(self, claimed_in=None) -> None:
        """Started there, its card claimed there (or in the main checkout), a file left
        uncommitted: the state a machine crash finds it in."""
        self.hook("session-start", self.hand, hook_event_name="SessionStart")
        self.hook("prompt-capture", self.hand, hook_event_name="UserPromptSubmit",
                  prompt="Validate the input to src/app.py")
        where = claimed_in or self.hand
        self.cli(where, "update", "0001", "--paths", "src/app.py",
                 "--done-when", "the parser rejects empty input")
        claimed = self.cli(where, "claim", "0001")
        self.assertEqual(0, claimed.returncode, f"precondition: {claimed.stderr}")
        self.pin(self.hand, os.getpid())
        if claimed_in is not None:
            self.pin(claimed_in, os.getpid())
        allowed = self.write_in(self.hand)
        self.assertNotEqual("deny", self.hook_decision(allowed), self.hook_reason(allowed))
        (self.hand / "src" / "draft.py").write_text("half = 1\n", encoding="utf-8")

    def restarted(self):
        """Every record names a process that is gone, and Claude Code resumed the chat in the
        main checkout, as it does when it declines to re-enter a tree."""
        gone = self.gone()
        for tree in (self.repo, self.hand):
            self.pin(tree, gone)
        return self.hook("session-start", self.repo, hook_event_name="SessionStart",
                         source="resume")

    def board(self, started) -> str:
        return json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]


class TestTheChatComesBackAfterItsMachineRestarted(HandTreeCase):
    def test_the_board_names_the_tree_it_left_and_no_allow_rule(self):
        self.working_in_the_hand_tree()
        board = self.board(self.restarted())
        self.assertIn(f"work happens in your own worktree `{self.hand.resolve()}`", board)
        self.assertIn("has this gate move it there first", board)
        self.assertNotIn("permissions.allow", board)

    def test_a_card_claimed_in_the_main_checkout_still_leads_back_to_it(self):
        """The card names the trunk; the record the chat left in the tree is the way back."""
        self.working_in_the_hand_tree(claimed_in=self.repo)
        self.assertIn(f"`{self.hand.resolve()}`", self.board(self.restarted()))

    def test_a_write_in_the_main_checkout_is_sent_to_that_tree_and_no_new_one(self):
        self.working_in_the_hand_tree()
        self.restarted()
        refused = self.write_in(self.repo)
        self.assertEqual("deny", self.hook_decision(refused))
        self.assertIn(str(self.hand.resolve()), self.hook_reason(refused))
        made = self.repo / ".claude" / "worktrees"
        self.assertFalse(made.is_dir() and any(made.iterdir()), "a tree was cut beside its own")

    def test_entering_it_moves_it_home_and_the_entry_after_asks_nobody(self):
        self.working_in_the_hand_tree()
        self.restarted()
        moved = self.enter(self.hand)
        self.assertEqual("deny", self.hook_decision(moved))
        self.assertIn(f"EnterWorktree with path {self.home}", self.hook_reason(moved))
        self.assertFalse(self.hand.exists())
        self.assertEqual("half = 1\n", (self.home / "src" / "draft.py").read_text("utf-8"),
                         "its uncommitted work went with it")
        self.assertEqual("feat/proj-topic",
                         git(["rev-parse", "--abbrev-ref", "HEAD"], self.home))
        entered = self.enter(self.home)
        self.assertEqual("allow", self.hook_decision(entered), self.hook_reason(entered))

    def test_its_card_is_its_own_in_the_tree_it_moved_to(self):
        self.working_in_the_hand_tree()
        self.restarted()
        self.enter(self.hand)
        again = self.write_in(self.home)
        self.assertNotEqual("deny", self.hook_decision(again), self.hook_reason(again))
        self.assertEqual(plan.DOING, plan.find(resolve(self.repo), "0001").state)


class TestALiveSessionsTreeMovesWithItsIdentity(HandTreeCase):
    """Not after a crash: the chat stepped back into the main checkout and enters again. Its
    record and card in the tree are carried to the new path, or the reaper would have handed
    its card back to the queue at the next session start."""

    def test_the_record_and_the_card_follow_the_tree(self):
        self.working_in_the_hand_tree()
        old = sid(self.hand, "S")
        self.enter(self.hand)
        ctx = resolve(self.repo)
        self.assertIsNone(sessions.get(ctx, old))
        card = plan.find(ctx, "0001")
        self.assertEqual((plan.DOING, sid(self.home, "S")), (card.state, card.owner))


class TestWhatKeepsATreeWhereItIs(HandTreeCase):
    """A move breaks whatever works in the tree, so the founder is asked as before."""

    def assert_left_alone(self, proc) -> None:
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))
        self.assertTrue(self.hand.is_dir())
        self.assertFalse(self.home.exists())

    def test_another_chats_live_session_in_it(self):
        self.pin(self.hand, ANOTHER_PROCESS, raw_id="B")
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        self.assert_left_alone(self.enter(self.hand))

    def test_a_process_working_in_it(self):
        server = subprocess.Popen(["sleep", "30"], cwd=str(self.hand))
        self.addCleanup(server.wait)
        self.addCleanup(server.kill)
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        self.assert_left_alone(self.enter(self.hand))

    def test_a_session_that_bypasses_permissions_is_asked_nothing_anyway(self):
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        self.assert_left_alone(self.enter(self.hand, permission_mode="bypassPermissions"))

    def test_a_tree_already_home_is_entered_as_it_is(self):
        self.home.parent.mkdir(parents=True)
        git(["worktree", "move", str(self.hand), str(self.home)], self.repo)
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        proc = self.enter(self.home)
        self.assertEqual("allow", self.hook_decision(proc), self.hook_reason(proc))
        self.assertTrue(self.home.is_dir())


class TestTheTreeOfItsCard(HandTreeCase):
    def test_a_card_on_the_trees_branch_leads_there_with_no_record_of_the_chat_in_it(self):
        from claude_bestpractice import worktree

        ctx = resolve(self.repo)
        me = sid(self.repo, "S")
        sessions.register(ctx, session_record_for(ctx, me, os.getpid()))
        card = plan.add(ctx, "the parser", paths=["src/app.py"], done_when="it rejects empty")
        plan.claim(ctx, card.id, me, "feat/proj-topic")
        self.assertEqual(self.hand.resolve(), worktree.own_tree(ctx, me))
