"""Every released version's state, read by the code that is here now.

The founder runs this plugin on real projects and updates it whenever a defect is fixed —
seven times in two days at one point. So "an upgrade does not break your repository" has to
be a fact this suite establishes, not a property nobody checks.

Reading the current code and reasoning about compatibility is exactly the method that has
failed every time in this project. So this does the other thing: it checks out each released
tag, runs THAT version's hooks against a real repository to produce state in that version's
own format, then points the CURRENT hooks at the result and requires them to work.

Nothing here is a fixture written by hand. A fixture is my belief about what v1.0.2 wrote;
v1.0.2 is what v1.0.2 wrote.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO_ROOT


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=120,
    )


def released_tags() -> list[str]:
    out = git(["tag", "--list", "v*", "--sort=version:refname"], REPO_ROOT).stdout.split()
    return [t for t in out if t.startswith("v1.")]


class TestStateFromEveryReleaseStillLoads(unittest.TestCase):
    """State lives in the founder's repository and outlives every plugin version.

    Tier A is committed and travels with the branch; Tier B sits in the git common
    directory. Neither is ever migrated by an install, so the code that reads them has to
    keep reading whatever an older version left behind — a session record without a field
    added later, a worktree record from before it carried a session id, a config with keys
    that did not exist yet.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tags = released_tags()
        if not cls.tags:
            raise unittest.SkipTest("no release tags fetched in this clone")

    def _plugin_at(self, tag: str, into: Path) -> Path:
        """That release's `plugin/` directory, extracted from the tag itself."""
        archive = into / f"{tag}.tar"
        with archive.open("wb") as handle:
            proc = subprocess.run(
                ["git", "archive", "--format=tar", tag, "plugin"],
                cwd=str(REPO_ROOT), stdout=handle, stderr=subprocess.PIPE, timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        shutil.unpack_archive(str(archive), str(into), format="tar")
        return into / "plugin"

    def _seed_repo(self, root: Path) -> Path:
        repo = root / "proj"
        repo.mkdir()
        git(["init", "-q"], repo)
        git(["config", "user.email", "t@t"], repo)
        git(["config", "user.name", "t"], repo)
        git(["config", "commit.gpgsign", "false"], repo)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        # The gates under test are not the point here; leaving them on would mean every
        # older version refused the writes and produced no state to carry forward.
        cfg = repo / ".claude" / "claude-bestpractice" / "config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"require_worktree": False, "protect_trunk": False}), encoding="utf-8")
        git(["add", "-A"], repo)
        git(["commit", "-qm", "seed"], repo)
        return repo

    def _run(self, plugin: Path, hook: str, event: dict, repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(plugin / "bin" / hook)],
            input=json.dumps(event), capture_output=True, text=True,
            cwd=str(repo), timeout=180,
        )

    def test_every_release_hands_over_to_the_current_one(self):
        for tag in self.tags:
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                old = self._plugin_at(tag, root)
                repo = self._seed_repo(root)

                # Produce state the way that release produced it.
                for hook, event in (
                    ("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}),
                    ("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                        "prompt": "add csv export to a.py"}),
                ):
                    event["cwd"] = str(repo)
                    proc = self._run(old, hook, event, repo)
                    self.assertEqual(proc.returncode, 0, f"{tag} {hook}: {proc.stderr[:300]}")

                # Now the version that is here, on top of it.
                current = REPO_ROOT / "plugin"
                started = self._run(
                    current, "session-start",
                    {"session_id": "s1", "hook_event_name": "SessionStart", "cwd": str(repo)},
                    repo,
                )
                self.assertEqual(started.returncode, 0, f"{tag} -> current: {started.stderr[:400]}")
                body = json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("health:", body, f"{tag} -> current: board did not render")

                # And a gate, because rendering a board is a lower bar than enforcing.
                denied = self._run(
                    current, "pre-tool",
                    {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                     "tool_input": {"file_path": str(repo / ".claude/claude-bestpractice/config.json"),
                                    "content": "{}"},
                     "cwd": str(repo)},
                    repo,
                )
                verdict = json.loads(denied.stdout or "{}").get("hookSpecificOutput", {})
                self.assertEqual(
                    verdict.get("permissionDecision"), "deny",
                    f"{tag} -> current: the protected-state gate stopped firing",
                )

    def test_the_current_version_is_tagged_or_unreleased(self):
        """A guard on this test's own premise: it compares against released tags, so it is
        worth knowing when the working tree's version is one of them."""
        from claude_bestpractice import __version__

        self.assertTrue(self.tags, "no tags to compare against")
        self.assertNotIn(
            f"v{__version__}", self.tags,
            "this version is already released; bump before shipping more changes",
        )


if __name__ == "__main__":
    unittest.main()
