"""`install.sh`, run the way a founder runs it, against stand-ins for everything it calls.

`claude` is a stub that accepts every subcommand, and the plugin it installs is a small
tree whose doctor always passes: what is under test is the installer's own decisions,
not the plugin's gates, which have their own suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO_ROOT, git

INSTALLER = REPO_ROOT / "install.sh"


class InstallerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-installer-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.stub("claude", '#!/bin/sh\n[ "$1" = --version ] && echo "2.1.281 (Claude Code)"\nexit 0\n')

    def stub(self, name: str, body: str) -> None:
        (self.stubs / name).write_text(body)
        (self.stubs / name).chmod(0o755)

    def install(self, path: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home),
               "PATH": path or f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True,
                              timeout=300, env=env, stdin=subprocess.DEVNULL)


class InstalledCase(InstallerCase):
    """A founder's machine with this plugin's clone already in the install directory."""

    def setUp(self) -> None:
        super().setUp()
        work = self.tmp / "upstream-work"
        doctor = work / "plugin" / "bin" / "claude-bp-doctor"
        doctor.parent.mkdir(parents=True)
        doctor.write_text("#!/usr/bin/env python3\nprint('All 1 checks passed.')\n")
        (work / "NOTES").write_text("upstream\n")
        git(["init", "-q", "-b", "main"], work)
        self.identify(work)
        git(["add", "-A"], work)
        git(["commit", "-qm", "the plugin"], work)
        self.upstream = self.tmp / "upstream.git"
        git(["clone", "-q", "--bare", str(work), str(self.upstream)], self.tmp)
        self.installed = self.home / ".claude-bestpractice"
        git(["clone", "-q", str(self.upstream), str(self.installed)], self.tmp)
        self.identify(self.installed)

    @staticmethod
    def identify(repo: Path) -> None:
        for key, value in (("user.email", "t@example.com"), ("user.name", "t"),
                           ("commit.gpgsign", "false")):
            git(["config", key, value], repo)


class TestARerunKeepsWorkOfYourOwn(InstalledCase):
    """Re-running the installer hard-reset the install directory to origin/HEAD.

    A commit and an uncommitted edit made there — a founder patching the plugin in place —
    were gone after a run that said nothing but "updating", with `reset: moving to
    origin/HEAD` in the reflog.
    """

    def test_a_local_commit_and_an_edit_survive_a_rerun(self):
        (self.installed / "LOCAL").write_text("a patch of my own\n")
        git(["add", "LOCAL"], self.installed)
        git(["commit", "-qm", "my patch"], self.installed)
        (self.installed / "NOTES").write_text("edited here, not committed\n")

        proc = self.install()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("my patch", git(["log", "-1", "--format=%s"], self.installed))
        self.assertEqual("edited here, not committed\n", (self.installed / "NOTES").read_text())

    def test_an_update_still_arrives_by_fast_forward(self):
        clone = self.tmp / "maintainer"
        git(["clone", "-q", str(self.upstream), str(clone)], self.tmp)
        self.identify(clone)
        (clone / "NEWS").write_text("a fix\n")
        git(["add", "NEWS"], clone)
        git(["commit", "-qm", "a fix upstream"], clone)
        git(["push", "-q", "origin", "main"], clone)
        (self.installed / "NOTES").write_text("edited here, not committed\n")

        proc = self.install()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("a fix upstream", git(["log", "-1", "--format=%s"], self.installed))
        self.assertEqual("edited here, not committed\n", (self.installed / "NOTES").read_text())


class TestTheIdFromBeforeTheRenameIsTakenOut(InstalledCase):
    """2.0.0 renamed the plugin from `claude-bestpractice` to `bestpractice`: Claude Code
    2.1.289 reserves plugin names that start `claude-` (decision 0029).

    Both ids load the same hooks. An upgrade that installed the new one beside the old would
    run every gate twice, two refusals for one call and two writers on one ledger, so the
    old one goes first.
    """

    def listing(self, *ids: str) -> None:
        """A `claude` that lists `ids` as installed and records every call it is given."""
        self.calls = self.tmp / "claude-calls"
        listed = "".join(f"  > {plugin_id}\\n    Version: 1.0.0\\n" for plugin_id in ids)
        self.stub("claude", (
            "#!/bin/sh\n"
            f'echo "$*" >> "{self.calls}"\n'
            'case "$1 $2" in\n'
            '  "--version "*) echo "2.1.289 (Claude Code)" ;;\n'
            f'  "plugin list") printf "Installed plugins:\\n\\n{listed}" ;;\n'
            "esac\n"
            "exit 0\n"
        ))

    def calls_made(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines()

    def test_the_former_id_is_uninstalled_before_the_new_one_goes_in(self):
        self.listing("claude-bestpractice@claude-bestpractice")
        proc = self.install()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        calls = self.calls_made()
        removed = calls.index("plugin uninstall claude-bestpractice@claude-bestpractice")
        added = calls.index("plugin install bestpractice@claude-bestpractice")
        self.assertLess(removed, added)

    def test_the_new_id_is_not_mistaken_for_the_former_one(self):
        """`bestpractice@claude-bestpractice` is the tail of the former id, so a plain grep
        for it matches both."""
        self.listing("claude-bestpractice@claude-bestpractice")
        self.install()
        self.assertNotIn("plugin uninstall bestpractice@claude-bestpractice", self.calls_made())

    def test_an_install_under_the_new_id_is_refreshed_and_nothing_else_removed(self):
        self.listing("bestpractice@claude-bestpractice")
        proc = self.install()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        calls = self.calls_made()
        self.assertIn("plugin uninstall bestpractice@claude-bestpractice", calls)
        self.assertNotIn("plugin uninstall claude-bestpractice@claude-bestpractice", calls)


class TestTheInstallerNamesThePythonItFound(InstallerCase):
    """With python3 at 3.8 and a `python` beside it, the installer said "found `python` but
    not `python3`" and offered a symlink that could not help: the old python3 stays the
    first one `env python3` finds."""

    def test_an_old_python3_is_named_as_old_not_as_missing(self):
        self.stub("python3", '#!/bin/sh\n[ "$1" = --version ] && echo "Python 3.8.10" && exit 0\nexit 1\n')
        self.stub("python", '#!/bin/sh\necho "Python 3.11.2"\n')
        path = os.pathsep.join([str(self.stubs), str(Path(shutil.which("git")).parent), "/usr/bin", "/bin"])
        proc = self.install(path)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("Python 3.8.10", proc.stderr)
        self.assertNotIn("but not `python3`", proc.stderr)


if __name__ == "__main__":
    unittest.main()
