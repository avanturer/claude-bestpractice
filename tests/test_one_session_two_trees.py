"""One session, two identities: it files its card in the main checkout and works in its tree.

Identity is (harness id, worktree), and Claude Code reports the tree as the hook's working
directory from the first call after `cd` or `EnterWorktree` into it — measured on 2.1.280 and
2.1.281. So a session that did everything this plugin asks, in the order it asks it — file a
card, claim it, be refused in the main checkout, move into the tree it was handed — arrived
there as a second identity, with the first still live because it names the same process. Its
own card was nobody's it knew, `claim` refused it as held by a live session that was itself,
`git add -A` was refused over its own paths, the database gate refused it over itself, and
`git worktree remove` of its own tree was run by the shell standing in it.

Driven through the real gates with the hook's working directory in the tree, and every rule
relaxed here is also run against a genuine sibling — another process — because uniting on
anything looser hides the sibling the per-tree identity exists to surface.
"""

from __future__ import annotations

import os
import unittest

from helpers import BIN, RepoCase, git, session_record_for, sid

from claude_bestpractice import plan, sessions
from claude_bestpractice.gitctx import resolve

# The process the founder's session runs as. Under test the walk up the process tree finds
# the test runner — or, inside a Claude Code session, that session's own CLI — so it is named
# here instead of resolved, the way `resolve_owner` would find one CLI running one chat.
THIS_PROCESS = os.getpid()

# Alive for the whole run and never this process: a sibling. `claude -p` children inherit the
# harness id of whatever launched them, so a sibling can carry THIS session's harness id too.
ANOTHER_PROCESS = 1

DSN = "postgres://localhost:5432/app"


class MovedSession(RepoCase):
    """A session started in the main checkout that claimed its card there, was refused a
    write there, and now stands in the tree this plugin provisioned for it."""

    relax_git_policy = False

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "def parse(text):\n    return text.split(',')\n")
        self.commit("a history to branch from")
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        self.hook("prompt-capture", self.repo, hook_event_name="UserPromptSubmit",
                  prompt="Validate the input to src/app.py")
        self.cli(self.repo, "update", "0001", "--paths", "src/app.py",
                 "--done-when", "the parser rejects empty input")
        claimed = self.cli(self.repo, "claim", "0001")
        self.assertEqual(0, claimed.returncode, f"precondition: {claimed.stderr}")
        refused = self.write_in(self.repo)
        self.assertEqual("deny", self.hook_decision(refused), "precondition: main is refused")
        self.main = resolve(self.repo).worktree_root
        self.tree = self.main / ".claude" / "worktrees" / self.provisioned()
        self.pin(self.repo, "S", THIS_PROCESS)
        self.pin(self.tree, "S", THIS_PROCESS)
        # The founder's next message, which is what reaches the tree first in a real chat.
        self.hook("prompt-capture", self.tree, hook_event_name="UserPromptSubmit",
                  prompt="go on, and cover it with a test")

    def provisioned(self) -> str:
        made = sorted(p.name for p in (self.repo / ".claude" / "worktrees").iterdir())
        self.assertEqual(1, len(made), f"precondition: one tree provisioned, got {made}")
        return made[0]

    def pin(self, tree, raw_id: str, pid: int) -> None:
        """Record which process this identity runs as, keeping whatever else it knows."""
        ctx = resolve(tree)
        identity = sid(tree, raw_id)
        record = sessions.get(ctx, identity) or session_record_for(ctx, identity, pid)
        record.pid, record.pid_trust = pid, sessions.PID_TRUST_OWNER
        sessions.register(ctx, record)

    def a_sibling(self, tree, raw_id: str, paths=()) -> str:
        """A different process standing in `tree`, with the paths its founder named."""
        self.pin(tree, raw_id, ANOTHER_PROCESS)
        identity = sid(tree, raw_id)
        sessions.touch(resolve(tree), identity, task_paths=list(paths))
        return identity

    def hook(self, name: str, cwd, session: str = "S", **event):
        return self.run_hook(name, {"session_id": session, **event}, cwd=cwd)

    def bash(self, command: str, cwd=None, session: str = "S"):
        return self.hook("pre-tool", cwd or self.tree, session, hook_event_name="PreToolUse",
                         tool_name="Bash", tool_input={"command": command})

    def write_in(self, tree, relpath: str = "src/app.py", session: str = "S"):
        return self.hook("pre-tool", tree, session, hook_event_name="PreToolUse",
                         tool_name="Write",
                         tool_input={"file_path": str(tree / relpath), "content": "x = 1\n"})

    def cli(self, cwd, *args: str, session: str = "S"):
        import subprocess
        import sys

        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": session}
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(cwd), env=env,
                              timeout=120)

    def edit_in_tree(self) -> None:
        (self.tree / "src" / "app.py").write_text(
            "def parse(text):\n    if not text:\n        raise ValueError('empty')\n"
            "    return text.split(',')\n", encoding="utf-8")


class TestItsCardComesWithIt(MovedSession):
    def test_its_own_card_covers_a_write_in_its_own_tree(self):
        proc = self.write_in(self.tree)
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_its_subagent_writes_under_the_same_card(self):
        """A subagent's calls carry the parent's session id plus an `agent_id` (measured on
        2.1.281), and it runs in the parent's process: the same session, the same card."""
        proc = self.hook("pre-tool", self.tree, hook_event_name="PreToolUse", tool_name="Write",
                         agent_id="a98bf2d12ecbaa42b", agent_type="general-purpose",
                         tool_input={"file_path": str(self.tree / "src" / "app.py"),
                                     "content": "x = 1\n"})
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_claiming_it_again_hands_it_over_instead_of_refusing_it_as_a_siblings(self):
        """The remedy the refusal named: `claim` the card you are on. It answered "held by
        live session S-…" — itself — and the only way on was filing the same work twice."""
        proc = self.cli(self.tree, "claim", "0001")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(sid(self.tree, "S"), plan.find(self.ctx(), "0001").owner)

    def test_the_founders_next_message_files_no_second_card(self):
        """`open_for` asked whether THIS id held a card in flight; the card was the other
        id's, so the next message in the tree put the same work on the board again."""
        self.hook("prompt-capture", self.tree, hook_event_name="UserPromptSubmit",
                  prompt="and make the error message name the empty field")
        self.assertEqual(["0001"], [task.id for task in plan.load_all(self.ctx())])

    def test_the_finish_does_not_send_it_to_file_another_card(self):
        self.edit_in_tree()
        proc = self.hook("evidence-gate", self.tree, hook_event_name="Stop",
                         stop_hook_active=False)
        self.assertNotIn("Nothing on the board says this session is working", proc.stderr)

    def test_its_database_is_not_a_collision_with_itself(self):
        """The tree's `.env` is seeded from the main checkout's, and the only session in
        the main checkout was this one, under the id it left there."""
        (self.repo / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        (self.tree / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        proc = self.write_in(self.tree)
        self.assertNotIn("already using the database", self.hook_reason(proc))
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_removing_its_own_tree_from_inside_is_done_from_the_main_checkout(self):
        """Decision 0022, asked from where the session stands. `mine()` found no tree for
        the id it has in the tree, so the call was approved and run by the shell standing
        in the directory it deletes."""
        proc = self.bash(f"git worktree remove {self.tree}")
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        self.assertIn(f"{self.tree} is removed", self.hook_reason(proc))
        self.assertIn(f"cd {self.main}", self.hook_reason(proc))
        self.assertFalse(self.tree.is_dir(), "the gate did not remove the tree")


class TestASiblingIsStillASibling(MovedSession):
    """Every rule above relaxed for the same PROCESS under the same harness id, and for
    nothing looser: another process is refused exactly as before."""

    def test_the_same_harness_id_in_another_process_does_not_lend_its_card(self):
        """`claude -p` children inherit one harness id. The card claimed in the main
        checkout is now another process's, and this session holds nothing."""
        self.pin(self.repo, "S", ANOTHER_PROCESS)
        proc = self.write_in(self.tree)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("nothing on the board says this session is working", self.hook_reason(proc))
        claim = self.cli(self.tree, "claim", "0001")
        self.assertEqual(1, claim.returncode)
        self.assertIn("held by live session", claim.stderr)

    def test_a_sibling_on_the_same_database_is_still_refused(self):
        self.a_sibling(self.repo, "B")
        (self.repo / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        (self.tree / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        proc = self.write_in(self.tree)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("already using the database", self.hook_reason(proc))

    def test_a_tree_made_for_another_process_is_not_this_ones_to_remove(self):
        """The interception acts for the session standing in its own tree only; a tree the
        same harness id holds in another process goes to the cross-tree rule as before."""
        self.pin(self.repo, "S", ANOTHER_PROCESS)
        self.pin(self.tree, "S", ANOTHER_PROCESS)
        me = self.tmp / "mine"
        git(["worktree", "add", "-q", "-b", "feat/mine", str(me)], self.repo)
        self.pin(me, "S", THIS_PROCESS)
        self.bash(f"git worktree remove {self.tree}", cwd=me)
        self.assertTrue(self.tree.is_dir(), "the gate removed another process's tree")


class TestStagingItsOwnTree(MovedSession):
    """`git add -A` counted every live session's claims in every tree, this session's own
    earlier id included: the ordinary commit step in the tree this plugin sent it to was
    refused as carrying "somebody else's" work."""

    def test_the_commit_step_in_its_own_tree_is_not_refused_over_its_own_paths(self):
        self.edit_in_tree()
        for command in ("git add -A", "git add .", 'git commit -am "Reject empty input"'):
            proc = self.bash(command)
            self.assertNotEqual("deny", self.hook_decision(proc), f"{command}: {proc.stdout}")

    def test_a_sibling_in_another_tree_is_a_merge_not_a_refusal(self):
        """Its lease and its founder's paths name a file in ITS tree. Two trees are two
        files, and what comes of it is a merge (#163)."""
        theirs = self.tmp / "theirs"
        git(["worktree", "add", "-q", "-b", "feat/theirs", str(theirs)], self.repo)
        other = self.a_sibling(theirs, "B", paths=["src/app.py"])
        self.assertIsNone(sessions.acquire_lease(resolve(theirs), other, "src/app.py"))
        self.edit_in_tree()
        proc = self.bash("git add -A")
        self.assertNotEqual("deny", self.hook_decision(proc), proc.stdout)

    def test_a_sibling_standing_in_this_tree_still_refuses_the_sweep(self):
        other = self.a_sibling(self.tree, "B", paths=["src/app.py"])
        self.assertIsNone(sessions.acquire_lease(resolve(self.tree), other, "src/app.py"))
        self.edit_in_tree()
        proc = self.bash("git add -A")
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("src/app.py", self.hook_reason(proc))

    def test_its_own_lease_in_the_tree_is_never_somebody_elses(self):
        """Staged from the main checkout, into its tree: the lease it took while standing
        in the tree is under the tree's id, which is this session all the same."""
        self.assertIsNone(sessions.acquire_lease(resolve(self.tree), sid(self.tree, "S"),
                                                 "src/app.py"))
        self.edit_in_tree()
        proc = self.bash(f"cd {self.tree} && git add -A", cwd=self.repo)
        self.assertNotEqual("deny", self.hook_decision(proc), proc.stdout)


if __name__ == "__main__":
    unittest.main()
