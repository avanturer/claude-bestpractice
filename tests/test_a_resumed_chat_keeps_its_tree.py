"""A resumed chat owns the tree it worked in, and a removal ends no process a session started (#310).

> 3. `git -C <main> worktree remove <tree>` → refused: "operates on another session's worktree …
>    fuddy-f8 (6ae92e19) [another process of this chat] works there". `fuddy-f8` is this chat
>    before the restart.
> 4. `cd <tree>` and the same command → the gate removes the tree and reports "3 process(es)
>    still running there: bash, ssh, timeout". Those three background commands of this session
>    then end with exit code 144.

The chat's old CLI outlived its window on the same boot, so its pid stayed alive and every test of
life said so; its background commands were its children, still running in the tree.
"""

from __future__ import annotations

import os
import signal
import subprocess
import unittest
from pathlib import Path

from helpers import RepoCase, git, session_record_for, sid

from claude_bestpractice import sessions, worktree
from claude_bestpractice.gitctx import resolve


@unittest.skipUnless(Path("/proc").is_dir(), "processes are read through /proc")
class ResumedCase(RepoCase):
    """Chat S worked in a tree made beside the repository; its CLI lives on, orphaned."""

    relax_git_policy = False

    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        self.hand = self.tmp / "fuddy-kuper"
        git(["worktree", "add", "-q", "-b", "feat/kuper", str(self.hand)], self.repo)
        self.old_cli = self.detached()
        for tree in (self.repo, self.hand):
            self.pin(tree, self.old_cli)

    def detached(self) -> int:
        """A live process no ancestor of this one, as a CLI left behind by its window is."""
        out = subprocess.run(["sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"],
                             capture_output=True, text=True, check=True)
        pid = int(out.stdout.strip())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL) if worktree._alive(pid) else None)
        return pid

    def pin(self, tree, pid: int) -> None:
        ctx = resolve(tree)
        identity = sid(tree, "S")
        record = sessions.get(ctx, identity) or session_record_for(ctx, identity, pid)
        record.pid, record.pid_trust = pid, sessions.PID_TRUST_OWNER
        sessions.register(ctx, record)

    def hook(self, gate: str, cwd, **event):
        return self.run_hook(gate, {"session_id": "S", **event}, cwd=cwd)

    def resumed(self, source: str = "resume") -> None:
        """The chat back in a new process, the one these tests run in, in the main checkout."""
        self.hook("session-start", self.repo, hook_event_name="SessionStart", source=source)
        self.pin(self.repo, os.getpid())

    def remove(self, cwd):
        return self.hook("pre-tool", cwd, hook_event_name="PreToolUse", tool_name="Bash",
                         tool_input={"command": f"git -C {self.repo} worktree remove {self.hand}"})


class TestTheChatBeforeTheResumeIsItsPast(ResumedCase):
    def test_its_own_tree_is_not_another_sessions_from_the_main_checkout(self):
        self.assertFalse(sessions._related(self.old_cli, os.getpid()), "the fixture proves nothing")
        self.resumed()
        proc = self.remove(self.repo)
        self.assertNotIn("another session's worktree", self.hook_reason(proc))

    def test_from_inside_the_removal_goes_through(self):
        self.resumed()
        proc = self.remove(self.hand)
        self.assertIn("done —", self.hook_reason(proc))
        self.assertFalse(self.hand.exists())

    def test_without_a_resume_it_is_still_another_process(self):
        """Two live processes of one chat with no resume between them: a `claude -p` it
        started looks exactly so, and is a session of its own."""
        self.resumed(source="startup")
        proc = self.remove(self.repo)
        self.assertIn("another process of this chat", self.hook_reason(proc))

    def test_an_old_process_that_writes_again_is_live_again(self):
        self.resumed()
        sessions.touch(resolve(self.hand), sid(self.hand, "S"))
        proc = self.remove(self.repo)
        self.assertIn("another process of this chat", self.hook_reason(proc))

    def test_another_chats_resume_is_not_this_chats(self):
        self.run_hook("session-start", {"session_id": "T", "hook_event_name": "SessionStart",
                                        "source": "resume"}, cwd=self.repo)
        self.assertTrue(self.old_cli_is_live())

    def test_a_resume_whose_process_has_ended_decides_nothing(self):
        """Only a live process is the chat now. Its pid, taken since by a process of nobody's,
        reads as alive and unrelated, and its start time says it is not the one that resumed."""
        self.hook("session-start", self.repo, hook_event_name="SessionStart", source="resume")
        resumer = self.detached()
        self.pin(self.repo, resumer)
        self.assertFalse(self.old_cli_is_live(), "the fixture proves nothing")
        sessions.touch(resolve(self.repo), sid(self.repo, "S"),
                       pid_fingerprint="the start of a process gone since")
        self.assertTrue(self.old_cli_is_live())

    def old_cli_is_live(self) -> bool:
        ctx = resolve(self.hand)
        return sessions.is_live(ctx, sessions.get(ctx, sid(self.hand, "S")))

    def test_the_process_that_resumed_is_not_its_own_past(self):
        """`/resume` inside one CLI: the chat's records in both trees name that process."""
        self.pin(self.hand, os.getpid())
        self.resumed()
        ctx = resolve(self.hand)
        self.assertTrue(sessions.is_live(ctx, sessions.get(ctx, sid(self.hand, "S"))))

    def test_a_chat_first_seen_here_on_its_resume_is_resumed_too(self):
        """A resume into a tree the chat had no record in yet writes a fresh one."""
        sessions.unregister(resolve(self.repo), sid(self.repo, "S"))
        self.resumed()
        proc = self.remove(self.repo)
        self.assertNotIn("another session's worktree", self.hook_reason(proc))

    def test_a_process_the_chat_started_is_never_its_past(self):
        child = subprocess.Popen(["sleep", "300"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.pin(self.hand, child.pid)
        self.resumed()
        self.assertTrue(sessions._related(child.pid, os.getpid()))
        proc = self.remove(self.repo)
        self.assertIn("another process of this chat", self.hook_reason(proc))


class TestARemovalEndsNoProcessASessionStarted(ResumedCase):
    def sleeping(self, where: Path, **env: str) -> subprocess.Popen:
        proc = subprocess.Popen(["sleep", "300"], cwd=str(where), env={**os.environ, **env})
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def session_command(self, where: Path, harness: str = "S") -> subprocess.Popen:
        """A background command of a Claude Code session, as its Bash tool starts one."""
        return self.sleeping(where, CLAUDE_CODE_SESSION_ID=harness,
                             CLAUDE_CODE_CHILD_SESSION="1", CLAUDECODE="1")

    def release(self, notes: list[str] | None = None) -> bool:
        return worktree._release(resolve(self.repo), (str(self.hand), "feat/kuper"), Path(),
                                 notes=notes)

    def test_the_removal_is_refused_first_and_names_them(self):
        self.resumed()
        waiting = self.session_command(self.hand)
        proc = self.remove(self.hand)
        said = self.hook_reason(proc)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn(f"`sleep` (pid {waiting.pid})", said)
        self.assertIn(f"kill {waiting.pid}", said)
        self.assertIn("this chat", said)
        self.assertTrue(self.hand.is_dir())
        self.assertIsNone(waiting.poll(), "a session's command was ended")

    def test_a_removal_git_would_do_from_the_main_checkout_is_refused_too(self):
        """git kills nothing, and takes the directory from under the command without a word."""
        self.resumed()
        waiting = self.session_command(self.hand)
        proc = self.remove(self.repo)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn(f"kill {waiting.pid}", self.hook_reason(proc))

    def test_another_chats_command_is_named_and_not_offered_to_kill(self):
        self.resumed()
        theirs = self.session_command(self.hand, harness="another-chat")
        said = self.hook_reason(self.remove(self.hand))
        self.assertIn(f"pid {theirs.pid}", said)
        self.assertNotIn("kill", said)
        self.assertNotIn("session, this chat", said)
        self.assertIsNone(theirs.poll())

    def test_a_removal_the_plugin_decides_on_keeps_the_tree(self):
        """The Stop gate's finish, the sweep and the agents' trees all remove through
        `_release`: none of them takes a tree from under a session's command."""
        waiting = self.session_command(self.hand)
        notes: list[str] = []
        self.assertFalse(self.release(notes))
        self.assertTrue(self.hand.is_dir())
        self.assertIn(f"`sleep` (pid {waiting.pid})", "; ".join(notes))
        self.assertIsNone(waiting.poll())

    def test_the_sweep_says_why_it_leaves_the_tree(self):
        waiting = self.session_command(self.hand)
        why = worktree._why_it_stays(resolve(self.repo), self.hand, "feat/kuper", "")
        self.assertIn(f"`sleep` (pid {waiting.pid}), started by a Claude Code session", why)

    def test_a_server_from_the_founders_ide_terminal_is_still_stopped(self):
        """Decision 0059 stands for the founder's own servers. An IDE sets `CLAUDECODE=1` in
        its terminals, so that variable says nothing about who started one."""
        server = self.sleeping(self.hand, CLAUDECODE="1")
        self.assertTrue(self.release())
        self.assertEqual(-15, server.wait(timeout=10))

    def test_a_sessions_mcp_server_is_neither_stopped_nor_keeps_the_tree(self):
        """Claude Code gives its stdio MCP servers the session's id and not the child flag:
        they are the session, as its CLI is, which the removal never stopped."""
        server = self.sleeping(self.hand, CLAUDE_CODE_SESSION_ID="S", CLAUDECODE="1")
        self.assertTrue(self.release())
        self.assertFalse(self.hand.exists())
        self.assertIsNone(server.poll(), "the session's MCP server was stopped")

    def test_the_session_a_process_belongs_to_is_read_off_its_environment(self):
        procs = (self.session_command(self.tmp), self.sleeping(self.tmp, CLAUDE_CODE_CHILD_SESSION="1"),
                 self.sleeping(self.tmp, CLAUDE_CODE_SESSION_ID="S"), self.sleeping(self.tmp))
        self.assertEqual(["S", "-", "", ""], [worktree._session_of(p.pid) for p in procs])
        self.assertEqual([True, True, True, False],
                         [worktree._part_of_a_session(p.pid) for p in procs])
