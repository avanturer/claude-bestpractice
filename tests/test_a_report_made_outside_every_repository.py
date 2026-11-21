"""A defect is reported from wherever a refusal tells a session to report it (#379).

A session standing in a folder that is no repository was refused by a gate that judged the call
in the clone its shell had wandered into, was told to run `claude-bp-report defect`, and the
command answered "not inside a git repository". Outside every repository a report is kept in the
plugin's own directory on the machine, shown and sent from there and from every clone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from helpers import BIN, git, make_repo
from test_defects_seen_on_this_version import SeenOnVersionsCase

from claude_bestpractice import defects, hookio, migrate, store
from claude_bestpractice.gitctx import resolve

SAID = "the fan-out gate refused six web researchers as agents searching one tree"


class OutsideCase(SeenOnVersionsCase):
    def setUp(self) -> None:
        super().setUp()
        self.data = self.tmp / "plugin-data"
        self.nowhere = self.tmp / "no-repository"
        self.nowhere.mkdir()
        patched = mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(self.data),
                                               "GH_CONFIG_DIR": str(self.tmp / "no-gh")})
        patched.start()
        self.addCleanup(patched.stop)

    def report_from(self, where: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "claude-bp-report"), *args],
                              capture_output=True, text=True, cwd=str(where), timeout=60)


class TestAReportFromNoRepository(OutsideCase):
    def test_defect_is_recorded_on_the_machine(self):
        proc = self.report_from(self.nowhere, "defect", "--gate", "subagent_fanout", SAID)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("not inside a git repository", proc.stdout + proc.stderr)
        rows = store.read_jsonl(self.data / defects.DEFECTS_FILE)
        self.assertEqual(1, len(rows))
        self.assertEqual("subagent_fanout", rows[0]["gate"])
        self.assertEqual([], store.read_jsonl(store.tier_b(self.ctx(), defects.DEFECTS_FILE)))

    def test_list_from_there_shows_it(self):
        self.report_from(self.nowhere, "defect", "--gate", "subagent_fanout", SAID)
        proc = self.report_from(self.nowhere, "list")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("1 defect(s) captured", proc.stdout)
        self.assertIn("subagent_fanout", proc.stdout)

    def test_a_clone_shows_and_sends_it_and_marks_it_sent_where_it_was_kept(self):
        self.report_from(self.nowhere, "defect", "--gate", "subagent_fanout", SAID)
        self.assertIn("subagent_fanout", self.report("list").stdout)
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(self.issues_filed()))
        self.assertEqual([], defects.unsent(self.ctx()))
        self.assertEqual([], defects.unsent(None))
        # The sent row went beside the report, and nothing of the machine's into the clone.
        self.assertEqual([], store.read_jsonl(store.tier_b(self.ctx(), defects.DEFECTS_FILE)))
        kept = store.read_jsonl(self.data / defects.DEFECTS_FILE)
        self.assertTrue(kept[-1]["sent_at"])
        self.assertNotIn(defects.KEPT_IN, kept[-1])

    def test_send_from_there_files_it_once(self):
        self.report_from(self.nowhere, "defect", "--gate", "subagent_fanout", SAID)
        self.assertEqual(0, self.report_from(self.nowhere, "send").returncode)
        self.assertEqual(0, self.report("send").returncode)
        self.assertEqual(1, len(self.issues_filed()))

    def test_one_report_in_both_stores_is_filed_once(self):
        self.report_from(self.nowhere, "defect", "--gate", "subagent_fanout", SAID)
        self.assertEqual(0, self.report("defect", "--gate", "subagent_fanout", SAID).returncode)
        self.assertEqual(1, len(defects.unsent(self.ctx())))
        self.assertEqual(0, self.report("send").returncode)
        self.assertEqual(1, len(self.issues_filed()))
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_a_crash_outside_every_repository_is_kept_too(self):
        try:
            raise KeyError("artifact_globs")
        except KeyError as exc:
            defects.record(None, "pre-tool", exc)
        rows = store.read_jsonl(self.data / defects.DEFECTS_FILE)
        self.assertEqual(["pre-tool"], [r["gate"] for r in rows])


class TestAStrayDotGitIsNoTree(OutsideCase):
    """Before #187 was fixed, state was written into `<home>/.git/claude-bestpractice`, making a
    `.git` no repository is behind. Every folder under that home then read as in a tree."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".git" / "claude-bestpractice" / "sessions").mkdir(parents=True)
        (self.home / ".git" / "claude-bestpractice" / "sessions" / "old.json").write_text("{}")
        (self.home / ".git" / "info").mkdir()
        (self.home / ".git" / "info" / "exclude").write_text(
            "# claude-code-runtime\n**/.claude/scheduled_tasks.lock\n**/.claude/worktrees/\n")
        self.folder = self.home / "dev" / "hacks" / "notes"
        self.folder.mkdir(parents=True)
        self.clone = make_repo(self.home / "dev", "clone")

    def test_a_folder_under_it_is_placed_by_the_project_the_session_started_in(self):
        event = hookio.HookEvent({"cwd": str(self.folder), "session_id": "s1",
                                  "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                  "tool_input": {"command": "gh pr merge 5"}})
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.clone)}):
            placed = hookio.placed(event)
        self.assertEqual(str(self.clone), placed.place)

    def test_git_reads_neither_a_head_nor_a_gitdir_file_there(self):
        self.assertFalse(hookio.is_a_git_dir(self.home / ".git"))
        self.assertTrue(hookio.is_a_git_dir(self.clone / ".git"))
        tree = self.tmp / "linked"
        git(["worktree", "add", "-q", "-b", "linked", str(tree)], self.clone)
        self.assertTrue(hookio.is_a_git_dir(tree / ".git"))

    def test_the_repair_takes_it_away(self):
        said = migrate._drop_our_state_from_a_git_dir_that_is_no_repository(resolve(self.clone))
        self.assertIn("removed", said)
        self.assertFalse((self.home / ".git").exists())
        self.assertTrue((self.clone / ".git" / "HEAD").exists())
        self.assertEqual("", migrate._drop_our_state_from_a_git_dir_that_is_no_repository(
            resolve(self.clone)))

    def test_what_is_not_ours_is_left(self):
        (self.home / ".git" / "info" / "exclude").write_text("# claude-code-runtime\nsecret.txt\n")
        (self.home / ".git" / "notes.md").write_text("mine")
        said = migrate._drop_our_state_from_a_git_dir_that_is_no_repository(resolve(self.clone))
        self.assertIn("left", said)
        self.assertFalse((self.home / ".git" / "claude-bestpractice").exists())
        self.assertEqual("mine", (self.home / ".git" / "notes.md").read_text())
        self.assertIn("secret.txt", (self.home / ".git" / "info" / "exclude").read_text())

    def test_a_file_of_someone_elses_beside_the_runtime_excludes_keeps_it(self):
        (self.home / ".git" / "notes.md").write_text("mine")
        migrate._drop_our_state_from_a_git_dir_that_is_no_repository(resolve(self.clone))
        self.assertEqual("mine", (self.home / ".git" / "notes.md").read_text())
        self.assertTrue((self.home / ".git" / "info" / "exclude").exists())

    def test_a_line_of_someone_elses_in_the_excludes_keeps_it(self):
        (self.home / ".git" / "info" / "exclude").write_text("# mine\nsecret.txt\n")
        migrate._drop_our_state_from_a_git_dir_that_is_no_repository(resolve(self.clone))
        self.assertIn("secret.txt", (self.home / ".git" / "info" / "exclude").read_text())

    def test_a_real_repository_above_is_never_touched(self):
        outer = make_repo(self.tmp, "outer")
        (outer / ".git" / "claude-bestpractice").mkdir(exist_ok=True)
        inner = make_repo(outer, "inner")
        migrate._drop_our_state_from_a_git_dir_that_is_no_repository(resolve(inner))
        self.assertTrue((outer / ".git" / "claude-bestpractice").exists())
