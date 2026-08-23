"""Whether a fix can actually reach the person who installed this.

Every test here exists because the answer was no, and because nothing anywhere said so.
`claude plugin update` reported `already at the latest version (1.0.0)` over twenty-one
commits of fixes, and the founder's install was frozen with no observable difference
between "up to date" and "permanently stranded".
"""

from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, REPO_ROOT, RepoCase, sid

from claude_bestpractice import upgrade

TOOL = REPO_ROOT / "tools" / "check_shipped.py"


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60, check=False,
    ).stdout.strip()


class TestTheVersionIsTheUpdateKey(unittest.TestCase):
    """Measured against the real CLI, not inferred from its help text.

    A local marketplace, an install, a change to the source with the version left alone,
    then `claude plugin update`: the CLI answered "already at the latest version (1.0.0)"
    and the changed file never reached the cache. Bumping the version and repeating it
    fetched the change immediately. That experiment is what this gate encodes.
    """

    def test_the_gate_refuses_a_shipped_change_with_no_bump(self):
        with _clone() as repo:
            (repo / "plugin" / "lib" / "claude_bestpractice" / "probe.py").write_text(
                "X = 1\n", encoding="utf-8")
            proc = _run(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("probe.py", proc.stderr)

    def test_a_bump_satisfies_it(self):
        with _clone() as repo:
            (repo / "plugin" / "lib" / "claude_bestpractice" / "probe.py").write_text(
                "X = 1\n", encoding="utf-8")
            _bump(repo, "9.9.9")
            proc = _run(repo)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_change_outside_the_shipped_tree_needs_no_bump(self):
        """`plugin/` is what the marketplace copies; a README reaches a clone by pull.

        Widening this to the whole repository would make every documentation typo a
        release, and a gate that fires on things that do not matter gets switched off.
        """
        with _clone() as repo:
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            (repo / "tests" / "probe_test.py").write_text("X = 1\n", encoding="utf-8")
            proc = _run(repo)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_unadded_file_still_counts(self):
        """Caught by this gate missing its own first run: `git diff` skips untracked.

        A whole new module is the most consequential thing that can appear under
        `plugin/`, and it was the one shape the first version of this could not see.
        """
        with _clone() as repo:
            new = repo / "plugin" / "lib" / "claude_bestpractice" / "brand_new.py"
            new.write_text("X = 1\n", encoding="utf-8")
            self.assertIn("brand_new.py", git(["status", "--porcelain"], repo))
            proc = _run(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("brand_new.py", proc.stderr)

    def test_a_bump_the_cli_cannot_see_is_refused(self):
        """The CLI reads plugin.json. A bump only in the package is not a bump."""
        with _clone() as repo:
            manifest = repo / "plugin" / ".claude-plugin" / "plugin.json"
            init = repo / "plugin" / "lib" / "claude_bestpractice" / "__init__.py"
            init.write_text(
                init.read_text(encoding="utf-8").replace('__version__ = "', '__version__ = "9', 1),
                encoding="utf-8",
            )
            proc = _run(repo)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("plugin.json", proc.stderr)
            self.assertIn(json.loads(manifest.read_text(encoding="utf-8"))["version"], proc.stderr)

    def test_a_default_branch_with_a_slash_is_still_compared(self):
        """`refs/remotes/origin/claude/x` rsplit on the last slash gives `x`.

        That ref does not exist, so the gate reported "not fetched, NOT verified" and
        waved the change through — failing open on the one repository convention this
        project actually uses for every branch it makes. Found by these tests the moment
        the branch under them had a slash in its name.
        """
        with _clone() as repo:
            git(["checkout", "-q", "-b", "team/release"], repo)
            git(["update-ref", "refs/remotes/origin/team/release", "HEAD"], repo)
            git(["symbolic-ref", "refs/remotes/origin/HEAD",
                 "refs/remotes/origin/team/release"], repo)
            (repo / "plugin" / "lib" / "claude_bestpractice" / "probe.py").write_text(
                "X = 1\n", encoding="utf-8")
            proc = _run(repo)
            self.assertNotIn("NOT verified", proc.stdout)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_no_remote_fails_open_and_says_so(self):
        """A founder offline must still be able to run `make check`, and must not be
        told this was verified when nothing was compared."""
        with _clone() as repo:
            git(["remote", "remove", "origin"], repo)
            git(["update-ref", "-d", "refs/remotes/origin/HEAD"], repo)
            for ref in git(["for-each-ref", "--format=%(refname)", "refs/remotes"], repo).split():
                git(["update-ref", "-d", ref], repo)
            proc = _run(repo)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("NOT verified", proc.stdout)


class TestRunningIsNotInstalled(unittest.TestCase):
    """`claude plugin update` says `Restart to apply changes.` once, and nothing again.

    The new version lands in a sibling directory, the old one is marked `.orphaned_at`
    and left in place, and every running session keeps executing the old copy. A founder
    who updates to get a fix and does not get it cannot tell which thing went wrong.
    """

    def _cache(self, tmp: str, running: str, *siblings: str) -> Path:
        """The layout the CLI actually writes, reproduced from a real install:
        `cache/<marketplace>/<plugin>/<version>/`, one directory per version kept."""
        cache = Path(tmp) / "cache"
        for name in (running, *siblings):
            (cache / name).mkdir(parents=True, exist_ok=True)
        return cache / running

    def test_a_newer_sibling_means_this_session_is_stale(self):
        import tempfile

        from claude_bestpractice import upgrade

        with tempfile.TemporaryDirectory() as tmp:
            root = self._cache(tmp, "1.0.1", "1.0.2")
            self.assertEqual(upgrade.superseded_by(root), "1.0.2")
            self.assertIn("Restart", upgrade.stale_line(root))

    def test_an_older_sibling_is_not_a_reason_to_say_anything(self):
        import tempfile

        from claude_bestpractice import upgrade

        with tempfile.TemporaryDirectory() as tmp:
            root = self._cache(tmp, "1.0.1", "1.0.0")
            self.assertIsNone(upgrade.superseded_by(root))
            self.assertEqual(upgrade.stale_line(root), "")

    def test_ten_outranks_nine(self):
        """String comparison puts 1.0.10 behind 1.0.9 and would go quiet exactly when
        the founder most needs to hear that they are running old code."""
        import tempfile

        from claude_bestpractice import upgrade

        with tempfile.TemporaryDirectory() as tmp:
            root = self._cache(tmp, "1.0.9", "1.0.10")
            self.assertEqual(upgrade.superseded_by(root), "1.0.10")

    def test_an_orphan_marker_is_believed(self):
        import tempfile

        from claude_bestpractice import upgrade

        with tempfile.TemporaryDirectory() as tmp:
            root = self._cache(tmp, "1.0.1")
            (root / upgrade.ORPHAN_MARKER).write_text("1785789442308", encoding="utf-8")
            self.assertTrue(upgrade.orphaned(root))
            self.assertIn("Restart", upgrade.stale_line(root))

    def test_the_running_copy_is_found_by_walking_up_from_the_code(self):
        """The whole mechanism rests on this: the version is the directory name, and
        the directory is found by looking for the manifest the CLI unpacks beside it."""
        import tempfile

        from claude_bestpractice import upgrade

        with tempfile.TemporaryDirectory() as tmp:
            root = self._cache(tmp, "1.0.1")
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
            deep = root / "lib" / "claude_bestpractice" / "upgrade.py"
            deep.parent.mkdir(parents=True)
            deep.write_text("", encoding="utf-8")
            self.assertEqual(upgrade.install_root(deep), root)

    def test_a_development_checkout_says_nothing(self):
        """`make check` runs from a clone, where there is no version directory at all."""
        from claude_bestpractice import upgrade

        self.assertEqual(upgrade.stale_line(), "")

    def test_the_update_command_is_the_qualified_one(self):
        """The short form fails with `Plugin not found` while the plugin is installed."""
        from claude_bestpractice import upgrade

        self.assertIn("claude-bestpractice@claude-bestpractice", upgrade.update_command())


class _clone:
    """A throwaway clone of this repository with `origin/main` present to diff against."""

    def __enter__(self) -> Path:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "repo"
        subprocess.run(
            ["git", "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(path)],
            capture_output=True, timeout=300, check=True,
        )
        # The clone's origin is this working copy, whose HEAD may be a feature branch, so
        # anchor the comparison at a commit that definitely predates the change under test.
        git(["remote", "set-head", "origin", "--auto"], path)

        # The tool under test, as it is right now rather than as it was last committed —
        # otherwise these tests are green on a version of the gate nobody is running.
        (path / "tools").mkdir(exist_ok=True)
        (path / "tools" / TOOL.name).write_text(TOOL.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


def _bump(repo: Path, version: str) -> None:
    init = repo / "plugin" / "lib" / "claude_bestpractice" / "__init__.py"
    manifest = repo / "plugin" / ".claude-plugin" / "plugin.json"
    import re

    text = init.read_text(encoding="utf-8")
    init.write_text(
        re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text),
        encoding="utf-8",
    )
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["version"] = version
    manifest.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "check_shipped.py")],
        cwd=str(repo), capture_output=True, text=True, timeout=180,
    )


class TestACopyOutsideTheCacheIsStillTold(unittest.TestCase):
    """An `install.sh` install runs from a git checkout, so the running copy is not in a
    version directory and `superseded_by` — which only looks at SIBLINGS — answers None.

    The founder then updates through the marketplace, the CLI unpacks the new version
    somewhere else, and the session keeps running the checkout while the board says
    nothing. Reported as a session insisting it was 1.17.0 the day after an update and a
    full machine restart.
    """

    def cache(self, *versions: str) -> Path:
        home = Path(tempfile.mkdtemp(prefix="claude-bestpractice-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        for version in versions:
            (home / upgrade.CACHE / "someone" / "claude-bestpractice" / version).mkdir(
                parents=True, exist_ok=True)
        return home

    def test_a_newer_version_unpacked_elsewhere_is_found(self):
        home = self.cache("99.0.0")
        self.assertEqual("99.0.0", upgrade.newer_in_the_cache(home))

    def test_an_older_one_is_not_mistaken_for_newer(self):
        self.assertIsNone(upgrade.newer_in_the_cache(self.cache("0.0.1")))

    def test_the_newest_wins_when_several_are_lying_around(self):
        self.assertEqual("99.1.0", upgrade.newer_in_the_cache(self.cache("99.0.0", "99.1.0")))

    def test_another_plugin_is_not_ours(self):
        home = Path(tempfile.mkdtemp(prefix="claude-bestpractice-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / upgrade.CACHE / "someone" / "other-plugin" / "99.0.0").mkdir(parents=True)
        self.assertIsNone(upgrade.newer_in_the_cache(home))

    def test_the_board_says_it_even_from_a_checkout(self):
        """`root=None` here is the checkout case: no version directory to compare against."""
        said = upgrade.stale_line(root=None, home=self.cache("99.0.0"))
        self.assertIn("99.0.0", said)
        self.assertIn("Restart", said)

    def test_nothing_is_said_when_nothing_is_newer(self):
        self.assertEqual("", upgrade.stale_line(root=None, home=self.cache()))


if __name__ == "__main__":
    unittest.main()


class TestAnUpdateMidSessionReachesTheSession(RepoCase):
    """The board says "you are running X, Y is on disk" at session start, and could never
    say it afterwards — which is the one moment it is needed. The founder updates while
    sessions are running, the running hooks stay behind, and the next thing they see is
    the old behaviour they just paid for a fix to (#166).
    """

    def setUp(self) -> None:
        super().setUp()
        # A HOME per test: the sandbox one is shared for the whole process, so a version
        # unpacked by one test would still be there for the test asserting silence.
        self.home = Path(tempfile.mkdtemp(prefix="claude-bestpractice-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def prompt(self, text: str = "почини экспорт CSV, он падает на пустом наборе"):
        return subprocess.run(
            [sys.executable, str(BIN / "prompt-capture")],
            input=json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                              "prompt": text, "cwd": str(self.repo)}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
            env={**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)},
        )

    def unpack(self, version: str) -> None:
        (self.home / upgrade.CACHE / "someone" / "claude-bestpractice"
         / version).mkdir(parents=True, exist_ok=True)

    def notes(self) -> list[str]:
        from claude_bestpractice import inbox

        return [n.get("text", "") for n in inbox.pending(self.ctx(), sid(self.repo, "s1"))]

    def test_the_running_session_is_told_it_is_behind(self):
        self.unpack("99.0.0")
        self.prompt()
        said = " ".join(self.notes())
        self.assertIn("99.0.0", said)
        self.assertIn("Restart", said)

    def test_nothing_is_said_when_nothing_is_newer(self):
        self.prompt()
        self.assertEqual([], self.notes())

    def test_it_is_said_once_and_not_on_every_turn(self):
        """Deduplicated on the claim like every other fact: a founder who writes ten
        messages before restarting is told once, not ten times."""
        self.unpack("99.0.0")
        for _ in range(3):
            self.prompt()
        self.assertEqual(1, len([n for n in self.notes() if "99.0.0" in n]))
