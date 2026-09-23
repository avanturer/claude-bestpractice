"""Issues #215 and #216: a gate that blocks its own remedy, and "off" that was not off.

Two reports, one shape. A founder asked for the plugin to be switched off for a project;
the switch went into `.claude/settings.json`, the harness had already loaded the hooks, and
the worktree gate went on refusing writes and provisioning trees for the rest of the
session — while `claude-bp set require_worktree off` refused them from inside the session
that was being blocked (#215). And the database-isolation gate refused the very write its
own message prescribes, so the state it put a tree in could be entered and never left
(#216).

The tests drive the real hook executables, because both defects were in what the gate DID
rather than in what it computed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import BIN, RepoCase, git

from claude_bestpractice import config, worktree

PLUGIN = config.PLUGIN_KEY


class OffCase(RepoCase):
    def tool(self, name: str, tool_input: dict, cwd=None):
        return self.run_hook(
            "pre-tool",
            {"session_id": "s1", "hook_event_name": "PreToolUse",
             "tool_name": name, "tool_input": tool_input},
            cwd=cwd,
        )

    def settings(self, enabled: bool, rel: str = ".claude/settings.json") -> Path:
        return self.write(rel, json.dumps({"enabledPlugins": {PLUGIN: enabled}}))

    def a_write_in_the_main_checkout(self, name: str = "feature.py"):
        """The call #215 was refused for: a plain write, in a repository with one session."""
        return self.tool("Write", {"file_path": str(self.repo / name), "content": "x = 1\n"})


class TestOffMeansOffOnThisCall(OffCase):
    """The harness reads `enabledPlugins` at session start and keeps the hooks it loaded,
    so nothing here can unload one. What it can do is stand down — on this tool call,
    not after a restart.
    """

    # The worktree rule is what #215 was being refused by, so this class is one of the
    # few that must not opt out of it.
    relax_git_policy = False

    def setUp(self) -> None:
        super().setUp()
        self.write("seed.py", "x = 0\n")
        self.commit("seed a history so the worktree rule applies")

    def test_the_gate_refuses_while_it_is_on(self):
        """The precondition, so the test below is about the switch and not about luck."""
        self.assertEqual("deny", self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_the_project_settings_switch_stands_it_down(self):
        self.settings(enabled=False)
        self.assertNotEqual("deny", self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_the_local_settings_file_counts_too(self):
        self.settings(enabled=False, rel=".claude/settings.local.json")
        self.assertNotEqual("deny", self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_it_says_nothing_rather_than_approving(self):
        """A switched-off gate has no opinion: approving would still suppress the
        permission prompt the founder would otherwise be shown."""
        self.settings(enabled=False)
        self.assertIsNone(self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_enabled_true_is_not_a_switch(self):
        self.settings(enabled=True)
        self.assertEqual("deny", self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_the_config_key_stands_it_down_as_well(self):
        """The founder's other door, and the one `claude-bp set enabled off` writes."""
        self.configure(enabled=False)
        self.assertNotEqual("deny", self.hook_decision(self.a_write_in_the_main_checkout()))

    def test_the_stop_gate_stands_down_too(self):
        """A plugin switched off that still holds the turn open is not switched off."""
        self.write("unverified.py", "x = 2\n")
        self.configure(enabled=False)
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("standing down", proc.stderr)


class TestOnlyTheFounderThrowsIt(OffCase):
    """A gate the gated party can switch off is a suggestion. `config.json` is refused to
    sessions outright; the project settings file cannot be, because a session edits
    permissions, hooks and env in it all day — so exactly one key is guarded, in exactly
    the direction that switches enforcement off.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write("seed.py", "x = 0\n")
        self.commit("seed a history")

    def writing_settings(self, body: dict, rel: str = ".claude/settings.json"):
        return self.tool("Write", {"file_path": str(self.repo / rel),
                                   "content": json.dumps(body)})

    def test_a_session_cannot_switch_the_plugin_off(self):
        proc = self.writing_settings({"enabledPlugins": {PLUGIN: False}})
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("enabled off", self.hook_reason(proc))

    def test_the_founders_word_opens_that_door(self):
        """The mechanism every other switch uses: their line, captured by the hook that
        reads their messages, and then the session may carry it out."""
        self.run_hook("prompt-capture", {
            "session_id": "s1", "hook_event_name": "UserPromptSubmit",
            "prompt": "enabled off",
        })
        self.assertNotEqual("deny", self.hook_decision(
            self.writing_settings({"enabledPlugins": {PLUGIN: False}})
        ))

    def test_the_rest_of_that_file_is_not_this_plugin_s(self):
        proc = self.writing_settings({"permissions": {"allow": ["Bash(npm test:*)"]}})
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_switching_it_on_is_never_refused(self):
        self.assertNotEqual("deny", self.hook_decision(
            self.writing_settings({"enabledPlugins": {PLUGIN: True}})
        ))

    def test_an_edit_that_carries_only_the_fragment_is_caught(self):
        """An `Edit` writes a piece of the file, so there is no document to parse."""
        self.write(".claude/settings.json", json.dumps({"enabledPlugins": {PLUGIN: True}}))
        proc = self.tool("Edit", {
            "file_path": str(self.repo / ".claude" / "settings.json"),
            "old_string": f'"{PLUGIN}": true',
            "new_string": f'"{PLUGIN}": false',
        })
        self.assertEqual("deny", self.hook_decision(proc))


class TestNothingInTheConfigCanTakeTheSwitchAway(OffCase):
    """`config.load` raised on values a hand edit or a stray tool produces, and every gate
    calls it first. `{"subagent_fanout": "inf"}` refused every tool call in the repository
    with "gate failed (OverflowError…)", and the two commands that could have helped —
    `claude-bp status` and `claude-bp set enabled off` — died on the same traceback. The
    off switch was behind the thing it had to switch off.
    """

    CONFIG = ".claude/claude-bestpractice/config.json"

    # Each of these raised out of the reader. None is a directory where a file belongs.
    SHAPES = (
        ("a quoted infinity", CONFIG, '{"subagent_fanout": "inf"}'),
        ("a literal past the float range", CONFIG, '{"subagent_fanout": 1e999}'),
        ("not a number", CONFIG, '{"max_repeat_signature": "nan"}'),
        ("an integer too long for a float", CONFIG, '{"notes_after_calls": 1' + "0" * 400 + "}"),
        ("test scripts as a list", "package.json", '{"scripts": ["test"]}'),
        ("a test script that is a number", "package.json", '{"scripts": {"test": 1}}'),
        ("a manifest that is not an object", "package.json", '["test"]'),
        ("a directory where the config belongs", CONFIG, None),
        ("a directory where local settings belong", ".claude/settings.local.json", None),
    )

    def shaped(self, index: int, rel: str, content) -> Path:
        """A repository of its own for one shape, so no two of them can interact."""
        from helpers import make_repo

        repo = make_repo(self.tmp, f"shape-{index}", relax_git_policy=True)
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            path.unlink()
        if content is None:
            path.mkdir()
        else:
            path.write_text(content, encoding="utf-8")
        return repo

    def test_no_shape_makes_the_reader_raise(self):
        """The cause, asked of the reader itself, and of the switch built on it."""
        from claude_bestpractice.gitctx import resolve

        for index, (label, rel, content) in enumerate(self.SHAPES):
            with self.subTest(label):
                ctx = resolve(self.shaped(index, rel, content))
                self.assertIsInstance(config.load(ctx), config.Config)
                self.assertTrue(config.enforcing(ctx)[0])

    def test_no_shape_makes_the_write_gate_fail_closed(self):
        """What the founder met: every Write refused, whatever it was."""
        for index, (label, rel, content) in enumerate(self.SHAPES):
            with self.subTest(label):
                repo = self.shaped(index, rel, content)
                proc = self.tool("Write", {"file_path": str(repo / "a.py"), "content": "x = 1\n"},
                                 cwd=repo)
                self.assertNotIn("gate failed", proc.stdout + proc.stderr)

    def test_a_number_that_is_not_finite_is_the_default(self):
        """Where it did not raise it was worse: `"inf"` was taken as a lease that never
        expires, and `"nan"` compares false with everything, so a sweep read it as off."""
        self.configure(lease_ttl_seconds="inf", task_idle_hours="nan", subagent_fanout=1e999)
        cfg, complaints = config.load_checked(self.ctx())
        self.assertEqual((1800.0, 24.0, 3),
                         (cfg.lease_ttl_seconds, cfg.task_idle_hours, cfg.subagent_fanout))
        self.assertEqual(3, len(complaints), complaints)

    def test_status_and_the_switch_still_work_beside_a_bad_value(self):
        """The two doors the founder had, through the command line they would use."""
        import subprocess
        import sys

        self.configure(subagent_fanout="inf")

        def cli(*args):
            return subprocess.run([sys.executable, str(BIN / "claude-bp"), *args],
                                  cwd=str(self.repo), capture_output=True, text=True, timeout=120)

        status = cli("status")
        self.assertEqual(0, status.returncode, status.stderr)
        self.run_hook("prompt-capture", {
            "session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "enabled off",
        })
        switched = cli("set", "enabled", "off")
        self.assertEqual(0, switched.returncode, switched.stderr)
        self.assertFalse(config.enforcing(self.ctx())[0])


class TestTheIsolationGateDoesNotBlockItsOwnCure(OffCase):
    """#216. The refusal reads "set DATABASE_URL in .env to a database name nobody else
    holds, then `claude-bp database`" — and refused that write with the same message. The
    only thing that worked was running it from a directory outside the tree, which is a
    hole rather than a remedy, and deleting the file was refused too: a tree with no `.env`
    falls back to the shared database, so the gate held the door shut from both sides.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write("seed.py", "x = 0\n")
        # `.env` is ignored, as it is in every repository that has one — the credential
        # scan's exemption turns on that, and a tracked `.env` is a real leak.
        self.write(".gitignore", ".env\n")
        self.commit("seed a history")
        self.tree = self.repo.parent / "repo-topic"
        git(["worktree", "add", "-q", "-b", "fix/topic", str(self.tree)], self.repo)
        self.addCleanup(
            lambda: git(["worktree", "remove", "--force", str(self.tree)], self.repo)
        )
        (self.tree / worktree.ENV_FILE).write_text(
            "DATABASE_URL=postgresql://u:p@localhost:5432/shared\n", encoding="utf-8"
        )
        self.a_sibling_on_the_same_database()

    def a_sibling_on_the_same_database(self) -> None:
        """Another live session, in another tree, naming this tree's database."""
        from claude_bestpractice import sessions

        other = self.repo.parent / "repo-sibling"
        git(["worktree", "add", "-q", "-b", "fix/sibling", str(other)], self.repo)
        self.addCleanup(lambda: git(["worktree", "remove", "--force", str(other)], self.repo))
        (other / worktree.ENV_FILE).write_text(
            "DATABASE_URL=postgresql://u:p@localhost:5432/shared\n", encoding="utf-8"
        )
        from claude_bestpractice.gitctx import resolve

        sessions.adopt(resolve(other), "sibling")

    def in_the_worktree(self, name: str, tool_input: dict):
        return self.run_hook(
            "pre-tool",
            {"session_id": "topic", "hook_event_name": "PreToolUse",
             "tool_name": name, "tool_input": tool_input},
            cwd=self.tree,
        )

    def test_an_ordinary_write_is_still_refused(self):
        """The precondition: the collision this gate exists for is really there."""
        proc = self.in_the_worktree(
            "Write", {"file_path": str(self.tree / "app.py"), "content": "x = 1\n"}
        )
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("same one", self.hook_reason(proc))

    def test_writing_the_env_file_is_the_way_out_and_is_allowed(self):
        proc = self.in_the_worktree(
            "Write",
            {"file_path": str(self.tree / worktree.ENV_FILE),
             "content": "DATABASE_URL=postgresql://u:p@localhost:5432/repo_wt_topic\n"},
        )
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_appending_to_it_from_the_shell_is_allowed(self):
        """Exactly the line the report ran, from the directory the report ran it in."""
        proc = self.in_the_worktree("Bash", {
            "command": "printf '\\nDATABASE_URL=postgresql://u:p@localhost:5432/repo_wt_topic\\n' "
                       f">> {self.tree / worktree.ENV_FILE}",
        })
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_removing_it_is_an_exit_too(self):
        """After a merge the copy is rubbish — and in that repository it held an API key."""
        proc = self.in_the_worktree(
            "Bash", {"command": f"rm -f {self.tree / worktree.ENV_FILE}"}
        )
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_the_exemption_does_not_cover_a_second_file(self):
        """One command that writes `.env` AND source is not a cure; it is work."""
        proc = self.in_the_worktree("Bash", {
            "command": f"rm -f {self.tree / worktree.ENV_FILE} {self.tree / 'app.py'}",
        })
        self.assertEqual("deny", self.hook_decision(proc))


class TestTheDatabaseCommandUsesTheProjectsOwnWay(RepoCase):
    """`claude-bp database` is the second half of that refusal, and it answered "there is
    no psql on this machine" on a repository whose backend talks to Postgres through its
    own driver. The refusal itself already said where to look — "let the project do it,
    which is what worktree_setup is for" — and then did not run the line the founder had
    written there (#216).
    """

    def run_database(self):
        """With no `psql` reachable, which is the machine the report was filed from.

        Hidden through PATH rather than skipped when the runner happens to have one: a
        test that only runs on some machines is a test that proves nothing on the others,
        and this whole fallback exists for the machine that has no client.
        """
        import os
        import subprocess
        import sys

        import shutil

        # git stays reachable — the command resolves a repository before it does anything
        # else — and `psql` does not, which is the only difference that matters here.
        lean = self.tmp / "lean-path"
        lean.mkdir(exist_ok=True)
        for tool in ("git",):
            found = shutil.which(tool)
            if found and not (lean / tool).exists():
                (lean / tool).symlink_to(found)
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "database"],
            cwd=str(self.repo), capture_output=True, text=True, timeout=120,
            env={**os.environ, "PATH": str(lean)},
        )

    def test_the_projects_setup_runs_when_there_is_no_client(self):
        import sys

        self.write(worktree.ENV_FILE, "DATABASE_URL=postgresql://u:p@localhost:5432/mine\n")
        marker = self.repo / "created-by-the-project"
        self.configure(worktree_setup=[sys.executable, "-c",
                                       f"open({str(marker)!r}, 'w').write('done')"])
        proc = self.run_database()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(marker.exists(), "the project's own way was never run")
        self.assertIn("worktree_setup", proc.stdout)

    def test_a_project_with_no_setup_still_says_what_is_missing(self):
        self.write(worktree.ENV_FILE, "DATABASE_URL=postgresql://u:p@localhost:5432/mine\n")
        proc = self.run_database()
        self.assertEqual(1, proc.returncode)
        self.assertIn("worktree_setup", proc.stderr)


class TestTidyingUpIsNotAnIntrusion(RepoCase):
    """#218. A session that had finished, merged and was removing its own tree was refused:
    "this git command operates on the main checkout … Run it in your own tree." It moves no
    HEAD there, touches no index and discards nobody's work — it deletes another directory —
    and git will not remove a tree from inside it, so the advice named the one place the
    command cannot be run.
    """

    # The rule under test is gated on `require_worktree`, so this class keeps it on.
    relax_git_policy = False

    def git_call(self, command: str, cwd=None):
        return self.run_hook(
            "pre-tool",
            {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}},
            cwd=cwd,
        )

    def setUp(self) -> None:
        super().setUp()
        self.write("seed.py", "x = 0\n")
        self.commit("seed a history")
        self.mine = self.repo.parent / "repo-mine"
        git(["worktree", "add", "-q", "-b", "fix/mine", str(self.mine)], self.repo)
        self.addCleanup(
            lambda: git(["worktree", "remove", "--force", str(self.mine)], self.repo)
        )

    def test_removing_a_tree_from_the_main_checkout_is_not_an_operation_on_it(self):
        """`-C <main checkout>` is where the command runs, not what it changes."""
        proc = self.git_call(
            f"git -C {self.repo} worktree remove --force {self.mine}", cwd=self.mine
        )
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_pruning_is_not_destructive_either(self):
        proc = self.git_call(f"git -C {self.repo} worktree prune", cwd=self.mine)
        self.assertNotEqual("deny", self.hook_decision(proc))

    def test_a_real_reset_in_the_main_checkout_is_still_refused(self):
        """The rule this narrows is still the rule: nothing else moved."""
        proc = self.git_call(f"git -C {self.repo} reset --hard HEAD~1", cwd=self.mine)
        self.assertEqual("deny", self.hook_decision(proc))

    def test_a_reset_hiding_behind_a_worktree_read_is_still_refused(self):
        proc = self.git_call(
            f"git -C {self.repo} worktree list && git -C {self.repo} reset --hard", cwd=self.mine
        )
        self.assertEqual("deny", self.hook_decision(proc))


class TestTheTrunksOwnContentIsNotDrift(RepoCase):
    """#217. The suite was red because the tree lagged `origin/main`; the session brought
    exactly the merged files across, which greened it; scope drift called those files a
    change the task never mentioned; reverting turned the suite red again. Two gates asking
    for opposite things, and the drift refusal says in so many words that prose cannot
    answer it — so only a person could break the circle.

    `landed` was already the answer and was asking the wrong revision: HEAD, which is the
    content the session had NOT touched.
    """

    def test_content_taken_from_the_trunk_is_forgiven(self):
        from claude_bestpractice import evidence

        self.write("shared.py", "x = 1\n")
        self.commit("the trunk's content")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)  # a stand-in for the remote
        self.write("shared.py", "x = 2\n")
        self.commit("move on locally")
        # The session brings the file back to what the trunk holds, which is what greened
        # the suite in the report.
        (self.repo / "shared.py").write_text("x = 1\n", encoding="utf-8")

        self.assertEqual(["shared.py"], evidence.landed(self.ctx(), ["shared.py"]))

    def test_the_sessions_own_new_content_is_still_drift(self):
        from claude_bestpractice import evidence

        self.write("shared.py", "x = 1\n")
        self.commit("the trunk's content")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        (self.repo / "shared.py").write_text("x = 99  # this session's own\n", encoding="utf-8")

        self.assertEqual([], evidence.landed(self.ctx(), ["shared.py"]))


if __name__ == "__main__":
    unittest.main()
