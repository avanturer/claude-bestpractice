"""A removed tree's processes go with it (decision 0059).

A dev server started in a tree outlives `git worktree remove`: Metro, expo and uvicorn went on
holding the tree's port and its database, serving code no longer on disk.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import RepoCase

from claude_bestpractice import worktree

SLEEPS = [sys.executable, "-c", "import time; time.sleep(300)"]
DEAF = [sys.executable, "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"]


@unittest.skipUnless(Path("/proc").is_dir(), "processes are read through /proc")
class TestWhatRanInARemovedTree(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.tree = self.add_worktree("feat-x")

    def started(self, argv: list[str], where: Path) -> subprocess.Popen:
        proc = subprocess.Popen(argv, cwd=str(where))
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def remove(self) -> list[str]:
        notes: list[str] = []
        self.assertTrue(worktree._release(self.ctx(), (str(self.tree), "feat-x"), Path(),
                                          notes=notes))
        return notes

    def test_a_server_left_running_in_it_is_stopped_and_named(self):
        server = self.started(SLEEPS, self.tree)
        notes = self.remove()

        self.assertEqual(-15, server.wait(timeout=10))
        self.assertTrue([note for note in notes if "process(es) still running there" in note],
                        notes)

    def test_one_that_does_not_stop_when_asked_is_killed(self):
        server = self.started(DEAF, self.tree)
        with mock.patch.object(worktree, "STOP_GRACE_SECONDS", 0.5):
            self.remove()
        self.assertEqual(-9, server.wait(timeout=10))

    def test_a_process_anywhere_else_is_left_alone(self):
        elsewhere = self.started(SLEEPS, self.tmp)
        sibling = self.tmp / "feat-x-old"
        sibling.mkdir()
        beside = self.started(SLEEPS, sibling)
        self.remove()

        self.assertIsNone(elsewhere.poll())
        self.assertIsNone(beside.poll())

    def test_a_removal_git_refuses_stops_nothing(self):
        server = self.started(SLEEPS, self.tree)
        (self.tree / "unfinished.py").write_text("x = 1\n")

        self.assertFalse(worktree._release(self.ctx(), (str(self.tree), "feat-x"), Path()))
        self.assertIsNone(server.poll())

    def test_this_process_and_its_parents_are_spared(self):
        spared = worktree._this_and_its_parents()
        self.assertIn(os.getpid(), spared)
        self.assertIn(os.getppid(), spared)

    def test_a_claude_session_is_never_one_of_them(self):
        for argv in ([b"/home/u/.local/bin/claude", b"--resume"],
                     [b"node", b"/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]):
            self.assertTrue(worktree._a_claude_session(argv), argv)
        self.assertFalse(worktree._a_claude_session([b"node", b"node_modules/.bin/metro"]))
