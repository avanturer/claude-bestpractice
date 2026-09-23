"""Where the pre-push hook goes, and what it may do to whatever is already there.

The hook carries ONE repository's checks — its runner, its suite — so it belongs only in a
hooks directory that repository's own config names, and it must never cost the founder a
hook of theirs on the way in.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import BIN, RepoCase, git, make_repo

CLI = BIN / "claude-bp-ci"


class HookCase(RepoCase):
    """A repository whose HOME is its own, so a test's global git config is its alone."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True, text=True, cwd=str(cwd or self.repo), timeout=180,
        )

    def ours(self, path: Path) -> bool:
        """Whether a hook of this plugin's sits at `path`, read the way anyone would read it."""
        from claude_bestpractice import ci

        return path.is_file() and ci.MARKER in path.read_text()

    def push(self, repo: Path) -> subprocess.CompletedProcess:
        """Push `repo`'s main to a fresh bare remote, the way the founder's push goes."""
        bare = self.tmp / f"{repo.name}-remote.git"
        git(["init", "-q", "--bare", str(bare)], self.tmp)
        git(["remote", "add", "origin", str(bare)], repo)
        return subprocess.run(["git", "push", "origin", "main"], cwd=str(repo),
                              capture_output=True, text=True, timeout=180)


class TestAHooksDirectoryEveryRepositoryReads(HookCase):
    """A GLOBAL core.hooksPath was honoured as if it were this repository's own.

    `claude-bp-ci local` — and `init`, setup, every session start, and the doctor the
    installer runs — wrote a hook with THIS repository's suite baked in into the one
    directory git runs hooks from for every repository on the machine, moving the founder's
    own hook aside. The next push from an unrelated Node repository ran `python3 -m pytest`
    and was refused.
    """

    def setUp(self) -> None:
        super().setUp()
        self.shared = self.home / ".githooks"
        self.shared.mkdir()
        (self.home / ".gitconfig").write_text(f"[core]\n\thooksPath = {self.shared}\n")
        self.theirs = self.shared / "pre-push"
        self.theirs.write_text("#!/bin/sh\necho FOUNDERS-GLOBAL-HOOK\n")
        self.theirs.chmod(0o755)
        # A Python suite, so the hook this repository would bake names a runner of its own.
        self.write("tests/test_x.py", "def test_x():\n    assert True\n")

    def untouched(self) -> None:
        self.assertEqual(["pre-push"], sorted(p.name for p in self.shared.iterdir()))
        self.assertIn("FOUNDERS-GLOBAL-HOOK", self.theirs.read_text())

    def test_local_writes_nothing_where_every_repository_reads(self):
        said = self.cli("local").stdout
        self.untouched()
        self.assertIn("global git config", said)

    def test_no_session_start_arms_it_there_either(self):
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.untouched()

    def test_another_repository_still_pushes_on_its_own_hooks(self):
        """The outcome that broke: a repository with no suite of this one's was refused."""
        self.cli("local")
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        pushed = self.push(make_repo(self.tmp, "other"))
        self.assertEqual(0, pushed.returncode, pushed.stderr)
        self.assertIn("FOUNDERS-GLOBAL-HOOK", pushed.stdout + pushed.stderr)

    def test_the_way_out_it_names_is_one_that_works(self):
        """Decision 0020: the refusal leaves commands that run here, and running them arms
        this repository's gate without touching anybody else's hooks."""
        from claude_bestpractice import ci

        said = self.cli("local").stdout
        own = next(line.strip() for line in said.splitlines()
                   if line.strip().startswith("git config core.hooksPath"))
        subprocess.run(shlex.split(own), cwd=str(self.repo), check=True, timeout=60)
        self.assertIn("installed", self.cli("local").stdout)
        self.assertEqual(self.ctx().common_dir / "hooks", ci.hooks_dir(self.ctx()))
        self.assertTrue(ci.installed(self.ctx()))
        self.untouched()

    def test_status_says_why_nothing_checks_the_push(self):
        said = self.cli("status").stdout
        self.assertIn("local pre-push: OFF", said)
        self.assertIn("global git config", said)

    def test_an_upgrade_takes_an_old_hook_out_and_puts_the_founders_back(self):
        """What an older version left behind: ours in the shared directory, theirs beside
        it. Taken out on the next session start — with no opt-out recorded, because nobody
        declined the gate; it was only ever in the wrong place."""
        from claude_bestpractice import ci

        self.theirs.replace(self.shared / ci.DISPLACED_NAME)
        self.theirs.write_text(ci.hook_body(self.ctx()))
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.untouched()
        self.assertFalse(ci.declined(self.ctx()), "an opt-out nobody asked for was recorded")


class TestATildeIsTheHomeDirectory(HookCase):
    """`core.hooksPath=~/.githooks` was read without expanding the tilde.

    The hook went into a directory literally named `~` inside the repository: git never
    ran it, `claude-bp-ci status` called the gate ON, `git status` listed `?? ~/`, and a red
    `make check` pushed.
    """

    def setUp(self) -> None:
        super().setUp()
        git(["config", "core.hooksPath", "~/.githooks"], self.repo)

    def test_the_hook_goes_where_git_runs_it(self):
        from claude_bestpractice import ci

        self.cli("local")
        self.assertTrue((self.home / ".githooks" / ci.HOOK_NAME).is_file())
        self.assertFalse((self.repo / "~").exists())
        self.assertEqual("", git(["status", "--porcelain"], self.repo))

    def test_a_red_check_is_refused_through_it(self):
        self.write("Makefile", "check:\n\t@echo red; exit 1\n")
        self.commit("a red check")
        self.cli("local")
        self.assertNotEqual(0, self.push(self.repo).returncode, "a red check was pushed")

    def test_an_upgrade_removes_the_hook_the_old_reader_left_in_the_tree(self):
        from claude_bestpractice import ci

        stray = self.repo / "~" / ".githooks" / ci.HOOK_NAME
        stray.parent.mkdir(parents=True)
        stray.write_text(ci.hook_body(self.ctx()))
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertFalse((self.repo / "~").exists())

    def test_a_file_of_the_founders_there_is_left_alone(self):
        from claude_bestpractice import migrate

        theirs = self.repo / "~" / ".githooks" / "pre-push"
        theirs.parent.mkdir(parents=True)
        theirs.write_text("#!/bin/sh\necho mine\n")
        migrate.repair(self.ctx())
        self.assertEqual("#!/bin/sh\necho mine\n", theirs.read_text())


class TestSessionsThatArmTheGateTogether(HookCase):
    """Three to eight sessions start at once, and each one arms the gate.

    Looking at the hook's path and moving what was there were two steps. A session that
    looked before a sibling wrote its hook then moved THAT hook onto the founder's: their
    hook was gone, and ours was chained to itself, so the next push forked `sh` until it
    was killed. Measured with eight real `ensure()` calls released together: 2 of 25.
    """

    FOUNDERS = "#!/bin/sh\necho FOUNDERS-OWN-HOOK\n"

    def setUp(self) -> None:
        super().setUp()
        from claude_bestpractice import ci

        self.hook = ci.hook_path(self.ctx())
        self.chained = self.hook.parent / ci.DISPLACED_NAME

    def founders_hook(self) -> None:
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        self.hook.write_text(self.FOUNDERS)
        self.hook.chmod(0o755)

    def a_session_that_looked_too_early(self) -> tuple:
        """The second session, acting on a look taken before the first one wrote."""
        from claude_bestpractice import ci

        with mock.patch.object(ci, "installed", return_value=False):
            return ci.install(self.ctx())

    def test_the_founders_hook_survives_a_session_that_looked_too_early(self):
        from claude_bestpractice import ci

        self.founders_hook()
        ci.install(self.ctx())
        self.a_session_that_looked_too_early()
        self.assertEqual(self.FOUNDERS, self.chained.read_text(), "the founder's hook was destroyed")
        self.assertTrue(self.ours(self.hook))

    def test_our_hook_is_never_the_one_moved_aside(self):
        """With no hook of the founder's at all, the same race chained ours to ours."""
        from claude_bestpractice import ci

        ci.install(self.ctx())
        self.a_session_that_looked_too_early()
        self.assertFalse(self.ours(self.chained), "our hook is chained to itself")

    def test_a_hook_of_ours_chained_as_theirs_is_never_run(self):
        """What that race left in repositories that ran it: the hook no longer runs a chained
        copy of itself. The copy here only leaves a mark, so that failing this cannot fork."""
        from claude_bestpractice import ci

        ci.install(self.ctx())
        ran = self.tmp / "chained-copy-ran"
        self.chained.write_text(f"#!/bin/sh\n{ci.MARKER}\ntouch '{ran}'\n")
        self.chained.chmod(0o755)
        subprocess.run(["sh", str(self.hook)], cwd=str(self.repo), capture_output=True,
                       text=True, timeout=120)
        self.assertFalse(ran.exists(), "the hook ran a copy of itself")

    def test_an_upgrade_removes_that_copy_and_removal_never_restores_it(self):
        from claude_bestpractice import ci

        ci.install(self.ctx())
        self.chained.write_text(self.hook.read_text())
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertFalse(self.chained.exists())

        self.chained.write_text(self.hook.read_text())
        ci.remove(self.ctx())
        self.assertFalse(self.hook.exists(), "our own hook was put back as the founder's")

    def test_two_different_hooks_of_theirs_are_both_kept(self):
        """Chaining the one at the path would overwrite the one already chained. Neither is
        this plugin's to lose, so nothing is installed and the note says which to remove."""
        from claude_bestpractice import ci

        self.founders_hook()
        self.chained.write_text("#!/bin/sh\necho AN-OLDER-HOOK\n")
        changed, note = ci.install(self.ctx())
        self.assertFalse(changed)
        self.assertEqual(self.FOUNDERS, self.hook.read_text())
        self.assertIn("AN-OLDER-HOOK", self.chained.read_text())
        self.assertIn("claude-bp-ci local", note)

    def test_a_hook_their_tool_rewrote_is_chained_as_before(self):
        """husky and lefthook rewrite the hook they own: the same hook, already chained."""
        from claude_bestpractice import ci

        self.founders_hook()
        self.chained.write_text(self.FOUNDERS)
        changed, _ = ci.install(self.ctx())
        self.assertTrue(changed)
        self.assertTrue(self.ours(self.hook))
        self.assertEqual(self.FOUNDERS, self.chained.read_text())


if __name__ == "__main__":
    unittest.main()
