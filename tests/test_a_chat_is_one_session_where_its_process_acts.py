"""A chat is one session wherever its own process acts (#312, #313, #319, #320, #321, #315, #361).

> After its PR was merged, a session left its worktree with ExitWorktree (action keep) and ran
> `git worktree remove --force <its tree>` from the main checkout. The gate refused: "fuddy-df
> (c7775792) [another process of this chat, such as a `claude -p` it started] works there".
> c7775792 is this very session.

The chat had resumed in a new process while its old CLI ran on (#310), and gone back into its
tree. Its calls there wrote the old CLI's record, so the record read as that CLI writing again:
live, and another process. Its agent's tree held the same record, and the card the agent was
handed covered nothing there. After `/clear` the record the old conversation left in the tree
named the very files being staged, and was read as a sibling's.

Every hook here runs under a process named `claude` that stays up, as Claude Code runs them, so
the process each hook stamps is the one the test means, on CI and inside a session alike.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import BIN, RepoCase, git, sid
from test_one_session_two_trees import UNRESOLVED, MovedSession

from claude_bestpractice import plan, sessions
from claude_bestpractice.gitctx import resolve

# What the stand-in CLI runs: each line it reads is a hook to run one shell down, in a directory,
# with an event on its stdin; each line it writes is what that hook printed.
SERVES_HOOKS = textwrap.dedent("""\
    import json, subprocess, sys
    for line in sys.stdin:
        asked = json.loads(line)
        done = subprocess.run(["sh", "-c", '"$0" "$1"', sys.executable, asked["gate"]],
                              input=json.dumps(asked["event"]), cwd=asked["cwd"],
                              capture_output=True, text=True)
        print(json.dumps({"returncode": done.returncode, "stdout": done.stdout,
                          "stderr": done.stderr}), flush=True)
""")


@unittest.skipUnless(Path("/proc").is_dir(), "the walk to the CLI reads /proc")
class ChatCase(RepoCase):
    """Chat S worked in a tree made by hand under its first CLI, holding a card there."""

    relax_git_policy = False

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        self.hand = self.tmp / "fuddy-fb-dark"
        git(["worktree", "add", "-q", "-b", "feat/dark", str(self.hand)], self.repo)
        self.old = self.a_cli()
        self.through(self.old, "session-start", self.repo, hook_event_name="SessionStart",
                     source="startup")
        self.through(self.old, "prompt-capture", self.repo, hook_event_name="UserPromptSubmit",
                     prompt="Make src/app.py dark")
        self.bash(self.old, self.hand, "git status")
        self.cli(self.hand, "update", "0001", "--paths", "src/app.py", "--done-when", "it is dark")
        claimed = self.cli(self.hand, "claim", "0001")
        self.assertEqual(0, claimed.returncode, f"precondition: {claimed.stderr}")
        self.assertEqual(self.old.pid, self.record(self.hand).pid, "precondition: the old CLI")

    def a_cli(self) -> subprocess.Popen:
        """A live process named `claude`, as the walk up from a hook finds the CLI."""
        home = self.tmp / f"cli-{len(list(self.tmp.glob('cli-*')))}"
        home.mkdir()
        claude = home / "claude"
        claude.write_text(f'#!/bin/sh\n"{sys.executable}" "$@"\n', encoding="utf-8")
        claude.chmod(0o755)
        (home / "serve.py").write_text(SERVES_HOOKS, encoding="utf-8")
        proc = subprocess.Popen([str(claude), str(home / "serve.py")], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.addCleanup(lambda: os.kill(proc.pid, signal.SIGKILL) if proc.poll() is None else None)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stdin.close)
        return proc

    def through(self, cli: subprocess.Popen, gate: str, cwd, session: str = "S", **event):
        """Run a hook under `cli`, as Claude Code runs it under the chat's CLI."""
        asked = {"gate": str(BIN / gate), "cwd": str(cwd),
                 "event": {"session_id": session, "cwd": str(cwd), **event}}
        cli.stdin.write(json.dumps(asked) + "\n")
        cli.stdin.flush()
        said = json.loads(cli.stdout.readline())
        return subprocess.CompletedProcess(gate, said["returncode"], said["stdout"], said["stderr"])

    def bash(self, cli, cwd, command: str, **event):
        return self.through(cli, "pre-tool", cwd, hook_event_name="PreToolUse", tool_name="Bash",
                            tool_input={"command": command}, **event)

    def write_in(self, cli, tree, relpath: str = "src/app.py", **event):
        return self.through(cli, "pre-tool", tree, hook_event_name="PreToolUse", tool_name="Write",
                            tool_input={"file_path": str(tree / relpath), "content": "x = 2\n"},
                            **event)

    def cli(self, cwd, *args: str):
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "S"}
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(cwd), env=env, timeout=120)

    def resumed(self, where=None) -> subprocess.Popen:
        """The chat back in a new process, the old CLI still running with nobody in it."""
        new = self.a_cli()
        self.through(new, "session-start", where or self.repo, hook_event_name="SessionStart",
                     source="resume")
        return new

    def record(self, tree) -> sessions.SessionRecord:
        return sessions.get(resolve(tree), sid(tree, "S"))

    def remove(self, cli, option: str = ""):
        return self.bash(cli, self.repo, f"git -C {self.repo} worktree remove {option}{self.hand}")


class TestBackInItsTree(ChatCase):
    """#312, #313, #319, #351: resumed in the main checkout, then into its tree and out again."""

    def setUp(self) -> None:
        super().setUp()
        self.new = self.resumed()
        self.bash(self.new, self.hand, "git status")

    def test_the_record_there_names_the_process_it_runs_in_now(self):
        self.assertEqual(self.new.pid, self.record(self.hand).pid)

    def test_it_removes_its_tree_from_the_main_checkout(self):
        for option in ("", "--force "):
            proc = self.remove(self.new, option)
            self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_it_merges_in_its_tree_from_the_main_checkout(self):
        proc = self.bash(self.new, self.repo, f"git -C {self.hand} merge --ff-only main")
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_it_claims_its_card_from_the_main_checkout(self):
        claimed = self.cli(self.repo, "claim", "0001")
        self.assertEqual(0, claimed.returncode, claimed.stderr)
        self.assertEqual(sid(self.repo, "S"), plan.find(self.ctx(), "0001").owner)


class TestNotBackInItsTreeYet(ChatCase):
    def test_a_merge_there_refused_for_want_of_a_card_names_the_claim(self):
        """The card is held under the tree's id, from before the resume: the merge names no
        file, and the card it can take back is the chat's."""
        new = self.resumed()
        proc = self.bash(new, self.repo, f"git -C {self.hand} merge feat/the-header")
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("claude-bp-plan claim 0001", self.hook_reason(proc))

    def test_another_chats_card_is_not_offered_as_this_ones(self):
        new = self.resumed()
        ctx = resolve(self.hand)
        taken, why = plan.claim(ctx, "0001", sid(self.hand, "B"), "feat/dark")
        self.assertIsNotNone(taken, why)
        said = self.hook_reason(self.write_in(new, self.hand))
        self.assertNotIn("claude-bp-plan claim 0001", said)


class TestTheOldProcessWritingAgain(ChatCase):
    def test_it_is_live_again_and_the_chat_does_not_take_its_record(self):
        """Decision 0091 kept: the old CLI's own write after the resume is that process at
        work, and the chat going back into the tree then is two processes there."""
        new = self.resumed()
        self.bash(self.old, self.hand, "git status")
        self.bash(new, self.hand, "git status")
        self.assertEqual(self.old.pid, self.record(self.hand).pid)
        proc = self.remove(new)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("another process of this chat", self.hook_reason(proc))


class TestEveryHookStampsTheChatsProcess(ChatCase):
    """Only the tool gate stamped: the founder's next message or a finish heard in the tree
    wrote the old CLI's record as that CLI, and the tree was another process's from then on."""

    def setUp(self) -> None:
        super().setUp()
        self.new = self.resumed()

    def test_the_founders_message_heard_in_its_tree(self):
        self.through(self.new, "prompt-capture", self.hand, hook_event_name="UserPromptSubmit",
                     prompt="and the header too")
        self.assertEqual(self.new.pid, self.record(self.hand).pid)
        self.assertNotEqual("deny", self.hook_decision(self.remove(self.new)))

    def test_a_finish_heard_in_its_tree(self):
        self.through(self.new, "evidence-gate", self.hand, hook_event_name="Stop",
                     stop_hook_active=False)
        self.assertEqual(self.new.pid, self.record(self.hand).pid)


class TestAResumeHeardInItsTree(ChatCase):
    """The resume fired while the chat stood in its tree: the main checkout's record kept the
    old CLI, and the chat's first call there was refused its own tree."""

    def test_it_removes_its_tree_from_the_main_checkout(self):
        new = self.resumed(self.hand)
        proc = self.remove(new, "--force ")
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))
        self.assertEqual(new.pid, self.record(self.repo).pid)


class TestWithoutAResume(ChatCase):
    def test_another_process_of_the_chat_keeps_its_record(self):
        """A `claude -p` the chat starts carries its id, starts and never resumes: a session of
        its own, whose record a call of the chat's must not take (decisions 0032, 0091)."""
        other = self.a_cli()
        self.through(other, "session-start", self.repo, hook_event_name="SessionStart",
                     source="startup")
        self.bash(other, self.hand, "git status")
        self.assertEqual(self.old.pid, self.record(self.hand).pid)
        proc = self.remove(other)
        self.assertIn("another process of this chat", self.hook_reason(proc))


class TestItsAgentsTree(ChatCase):
    """#320, #321: the agent worked in its own tree under the old CLI before the drop."""

    def setUp(self) -> None:
        super().setUp()
        made = self.through(self.old, "worktree-create", self.repo,
                            hook_event_name="WorktreeCreate", name="side-quest")
        self.made = Path(made.stdout.strip())
        self.assertTrue(self.made.is_dir(), f"precondition: the hook made a tree: {made.stderr}")
        self.bash(self.old, self.made, "git status", agent_id="a1", agent_type="general-purpose")
        self.assertTrue(self.record(self.made).task_statement, "precondition: it was told")

    def agent_writes(self, cli):
        return self.write_in(cli, self.made, agent_id="a1", agent_type="general-purpose")

    def test_it_writes_the_files_of_the_card_its_chat_handed_it(self):
        new = self.resumed()
        self.assertEqual(0, self.cli(self.repo, "claim", "0001").returncode)
        handed = plan.held_by(self.ctx(), sid(self.repo, "S"))
        self.assertEqual(["0001"], [card.id for card in handed],
                         "precondition: SubagentStart hands it this card")
        proc = self.agent_writes(new)
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))
        claimed = self.cli(self.made, "claim", "0001")
        self.assertEqual(0, claimed.returncode, claimed.stderr)


class TestACardItsPastHolds(ChatCase):
    """#315, #320: the agent claimed the card in its own tree; the chat resumed and wrote."""

    def setUp(self) -> None:
        super().setUp()
        made = self.through(self.old, "worktree-create", self.repo,
                            hook_event_name="WorktreeCreate", name="side-quest")
        self.made = Path(made.stdout.strip())
        self.bash(self.old, self.made, "git status", agent_id="a1", agent_type="general-purpose")
        self.assertEqual(0, self.cli(self.made, "claim", "0001").returncode)
        self.new = self.resumed()

    def test_the_refusal_names_the_claim_that_takes_it_back(self):
        proc = self.write_in(self.new, self.hand)
        said = self.hook_reason(proc)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("claude-bp-plan claim 0001", said)
        self.assertNotIn("claude-bp-plan add", said)
        self.assertNotIn("claude-bp-plan update", said)
        claimed = self.cli(self.hand, "claim", "0001")
        self.assertEqual(0, claimed.returncode, claimed.stderr)
        again = self.write_in(self.new, self.hand)
        self.assertNotEqual("deny", self.hook_decision(again), self.hook_reason(again))

    def test_a_write_beside_the_card_is_not_sent_to_it(self):
        said = self.hook_reason(self.write_in(self.new, self.hand, "src/other.py"))
        self.assertNotIn("claude-bp-plan claim 0001", said)
        self.assertIn("claude-bp-plan add", said)

    def test_the_finish_names_the_claim_too(self):
        (self.hand / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
        proc = self.through(self.new, "evidence-gate", self.hand, hook_event_name="Stop",
                            stop_hook_active=False)
        self.assertIn("claude-bp-plan claim 0001", proc.stderr)

    def test_a_card_a_live_process_of_the_chat_holds_is_not_offered(self):
        """`claim` refuses it, and a refusal names only what runs (decision 0020)."""
        self.bash(self.old, self.made, "git status", agent_id="a1", agent_type="general-purpose")
        said = self.hook_reason(self.write_in(self.new, self.hand))
        self.assertNotIn("claude-bp-plan claim 0001", said)
        self.assertEqual(1, self.cli(self.hand, "claim", "0001").returncode)


class TestTheUpgradeHandsTheChatTheRecordsItWrote(ChatCase):
    """Repair 0050: the chat went back into its tree under an older version, whose calls there
    wrote the old CLI's record as that CLI's."""

    STEP = "0050-hand-a-resumed-chat-the-records-it-wrote"

    def as_an_older_version_left_it(self) -> subprocess.Popen:
        new = self.resumed()
        sessions.touch(resolve(self.hand), sid(self.hand, "S"))
        self.assertIn("another process of this chat", self.hook_reason(self.remove(new)),
                      "the fixture proves nothing")
        return new

    def repair(self) -> str:
        from claude_bestpractice import migrate

        return migrate._REPAIRS[self.STEP][1](self.ctx())

    def test_the_record_names_the_cli_the_chat_runs_in_now(self):
        new = self.as_an_older_version_left_it()
        self.assertIn("1 record(s)", self.repair())
        self.assertEqual(new.pid, self.record(self.hand).pid)
        self.assertNotEqual("deny", self.hook_decision(self.remove(new)))
        self.assertEqual("", self.repair(), "nothing changed, nothing written")

    def test_without_a_resume_nothing_is_taken(self):
        sessions.touch(resolve(self.hand), sid(self.hand, "S"))
        self.assertEqual("", self.repair())
        self.assertEqual(self.old.pid, self.record(self.hand).pid)

    def test_a_tree_it_never_went_back_into_is_left_as_its_past(self):
        made = Path(self.through(self.old, "worktree-create", self.repo,
                                 hook_event_name="WorktreeCreate", name="earlier").stdout.strip())
        self.bash(self.old, made, "git status")
        self.as_an_older_version_left_it()
        self.repair()
        self.assertEqual(self.old.pid, self.record(made).pid)
        self.assertFalse(sessions.is_live(resolve(made), self.record(made)))

    def test_a_resume_in_a_process_the_old_one_started_takes_nothing(self):
        """A process the old CLI started is its own descendant, as a `claude -p` is: never the
        chat come back (decision 0091)."""
        ctx = resolve(self.repo)
        child = subprocess.Popen(["sleep", "300"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        for tree, pid in ((self.hand, os.getpid()), (self.repo, child.pid)):
            sessions.touch(resolve(tree), sid(tree, "S"), pid=pid,
                           pid_fingerprint=sessions.pid_fingerprint(pid))
        sessions.touch(ctx, sid(self.repo, "S"), resumed_at=time.time())
        sessions.touch(resolve(self.hand), sid(self.hand, "S"))
        self.assertEqual("", self.repair())
        self.assertEqual(os.getpid(), self.record(self.hand).pid)

    def test_a_record_the_old_cli_made_after_the_resume_stays_its_own(self):
        self.as_an_older_version_left_it()
        made = Path(self.through(self.old, "worktree-create", self.repo,
                                 hook_event_name="WorktreeCreate", name="later").stdout.strip())
        self.bash(self.old, made, "git status")
        self.repair()
        self.assertEqual(self.old.pid, self.record(made).pid)


@unittest.skipUnless(Path("/proc").is_dir(), "the walk to the CLI reads /proc")
class TestAfterClear(RepoCase):
    """#361: `/clear` in one CLI, a new conversation id in the same process. The record the
    old conversation left in the tree named the files being staged."""

    relax_git_policy = False
    a_cli = ChatCase.a_cli
    through = ChatCase.through
    bash = ChatCase.bash

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        self.tree = self.tmp / "fuddy-offline-scan"
        git(["worktree", "add", "-q", "-b", "feat/offline-scan", str(self.tree)], self.repo)
        self.cli = self.a_cli()
        for conversation, source in (("H1", "startup"), ("H2", "clear")):
            self.through(self.cli, "session-start", self.repo, conversation,
                         hook_event_name="SessionStart", source=source)
            self.bash(self.cli, self.tree, "git status", session=conversation)
        sessions.touch(resolve(self.tree), sid(self.tree, "H1"), task_paths=["src/offline.py"])
        (self.tree / "src" / "offline.py").write_text("y = 2\n", encoding="utf-8")

    def stage(self):
        return self.bash(self.cli, self.tree, "git add -A", session="H2")

    def test_its_own_files_are_staged(self):
        proc = self.stage()
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_it_removes_the_tree_from_the_main_checkout(self):
        proc = self.bash(self.cli, self.repo, f"git worktree remove --force {self.tree}",
                         session="H2")
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_the_card_the_conversation_before_held_is_not_this_ones(self):
        """Decision 0028 hands it back to the board once Claude Code says the CLI moved on."""
        ctx = resolve(self.tree)
        card = plan.add(ctx, "the offline scan", paths=["src/offline.py"], done_when="it scans")
        plan.claim(ctx, card.id, sid(self.tree, "H1"), "feat/offline-scan")
        self.assertEqual([], plan.held_by(ctx, sid(self.tree, "H2")))

    def test_another_process_naming_them_still_refuses_it(self):
        other = self.a_cli()
        self.bash(other, self.tree, "git status", session="B")
        sessions.touch(resolve(self.tree), sid(self.tree, "B"), task_paths=["src/offline.py"])
        proc = self.stage()
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("src/offline.py", self.hook_reason(proc))


class TestWithNoProcAgentAndParentAreOne(MovedSession):
    """#315 where no pid names a process: the agent claimed the card in the tree `worktree-
    create` made for it, and its parent wrote in the tree the gate made. Both trees were tied
    to the main checkout and not to each other."""

    def setUp(self) -> None:
        super().setUp()
        self.pin(self.repo, "S", UNRESOLVED, trust=sessions.PID_TRUST_PARENT)
        self.pin(self.tree, "S", UNRESOLVED + 1, trust=sessions.PID_TRUST_PARENT)
        self.made = self.a_tree_the_harness_asked_for()
        self.pin(self.made, "S", UNRESOLVED + 2, trust=sessions.PID_TRUST_PARENT)
        self.assertEqual(0, self.cli(self.made, "claim", "0001").returncode)

    def test_the_parent_writes_under_the_card_its_agent_claimed(self):
        proc = self.write_in(self.tree)
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_a_reset_in_the_agents_tree_is_still_refused(self):
        """The registry's word holds the card, never the tree: the record there may be a
        `claude -p`'s, and a reset takes its uncommitted work (decision 0032)."""
        proc = self.bash(f"git -C {self.made} reset --hard")
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout)

    def test_another_chat_in_the_agents_tree_is_still_another_session(self):
        self.pin(self.made, "B", UNRESOLVED + 3, trust=sessions.PID_TRUST_PARENT)
        self.assertNotIn(sid(self.made, "B"), sessions.identities(self.ctx(), sid(self.tree, "S")))


@unittest.skipUnless(Path("/proc").is_dir(), "ancestry is read through /proc")
class TestTheStampOfTheChatsPast(RepoCase):
    """`restamped` itself, in this process: the chat resumed here, its old CLI still running."""

    def setUp(self) -> None:
        super().setUp()
        out = subprocess.run(["sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"],
                             capture_output=True, text=True, check=True)
        self.old = int(out.stdout.strip())
        self.addCleanup(lambda: os.kill(self.old, signal.SIGKILL)
                        if sessions.pid_alive(self.old) else None)
        self.tree = self.add_worktree("fuddy-kuper")
        for tree in (self.repo, self.tree):
            record = sessions.SessionRecord(
                session_id=sid(tree, "S"), pid=self.old, pid_trust=sessions.PID_TRUST_OWNER,
                worktree=resolve(tree).worktree_root.as_posix(), branch="", baseline_commit="",
                started_at=0.0, heartbeat_at=0.0)
            sessions.register(resolve(tree), record)

    def written_by(self, pid: int) -> sessions.SessionRecord:
        ctx = resolve(self.tree)
        with mock.patch.object(sessions, "resolve_owner",
                               return_value=(pid, sessions.PID_TRUST_OWNER)):
            return sessions.restamped(ctx, sessions.get(ctx, sid(self.tree, "S")))

    def resume_here(self) -> None:
        sessions.restarted(self.ctx(), sid(self.repo, "S"), os.getpid(),
                           sessions.PID_TRUST_OWNER, "main", resumed=True)

    def test_a_record_the_chat_resumed_past_is_stamped_with_the_cli_writing_it(self):
        self.resume_here()
        self.assertEqual(os.getpid(), self.written_by(os.getpid()).pid)
        self.assertTrue(sessions.is_live(self.ctx(), sessions.get(resolve(self.tree),
                                                                  sid(self.tree, "S"))))

    def test_without_a_resume_it_stays_the_old_process(self):
        self.assertEqual(self.old, self.written_by(os.getpid()).pid)

    def test_the_old_process_writing_for_itself_keeps_it(self):
        self.resume_here()
        self.assertEqual(self.old, self.written_by(self.old).pid)


if __name__ == "__main__":
    unittest.main()
