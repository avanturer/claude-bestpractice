"""A defect report is sent only when the version that runs now has been seen to have it.

One `claude-bp-report send` filed fifty issues at once (#313–#362). They had been held unsent
for a month, captured on 2.2.0 to 2.14.3, and most were about defects that issues filed by hand
had already had fixed. A report says what one version did; what still holds is seen again, and
a sighting stamps the version it was seen on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from helpers import BIN, RepoCase

from claude_bestpractice import __version__, defects, store


class SeenOnVersionsCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.calls = self.stubs / "calls"
        # `gh` first on PATH: logs every call, prints `$FAKE_RELEASE` for `release view`,
        # and an issue link for `issue create`.
        (self.stubs / "gh").write_text(
            "#!/bin/sh\n"
            f"echo \"$*\" >> {self.calls}\n"
            'case "$1 $2" in\n'
            '  "release view") [ -n "$FAKE_RELEASE" ] || exit 1; echo "$FAKE_RELEASE";'
            ' exit "${FAKE_RELEASE_EXIT:-0}";;\n'
            '  "issue create") echo "https://github.invalid/issues/1";;\n'
            "esac\n"
        )
        (self.stubs / "gh").chmod(0o755)
        self.released(f"v{__version__}")

    def released(self, tag: str, exit_code: int = 0) -> None:
        patched = mock.patch.dict(os.environ, {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_RELEASE": tag,
            "FAKE_RELEASE_EXIT": str(exit_code),
        })
        patched.start()
        self.addCleanup(patched.stop)

    def captured(self, what: str) -> None:
        recorded, said = defects.observed(self.ctx(), what, "pre-tool")
        self.assertTrue(recorded, said)

    def captured_on(self, version: str, what: str) -> None:
        """A report as an older version wrote it: the row it left, its version and all."""
        with mock.patch.object(defects, "_version", return_value=version):
            self.captured(what)

    def report(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-report"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )

    def issues_filed(self) -> list[str]:
        if not self.calls.exists():
            return []
        return [c for c in self.calls.read_text().splitlines() if c.startswith("issue create")]


class TestAReportFromAnOlderVersionIsHeldBack(SeenOnVersionsCase):
    def test_a_report_last_seen_on_an_older_version_is_not_sent(self):
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.assertEqual([], defects.unsent(self.ctx()))
        self.assertEqual(1, len(defects.held_back(self.ctx())))
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], self.issues_filed())

    def test_what_is_held_back_is_said_with_the_versions_and_why(self):
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.captured_on("2.14.3", "the worktree gate refused this session's own tree")
        for args in ((), ("send",)):
            out = self.report(*args).stdout
            self.assertIn("2 held back", out)
            self.assertIn("2.2.0 to 2.14.3", out)
            self.assertIn(__version__, out)

    def test_a_report_from_this_version_is_sent_beside_held_ones(self):
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.assertEqual(["the evidence gate refused a turn whose suite had passed"],
                         [r["error"] for r in defects.unsent(self.ctx())])
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        filed = self.issues_filed()
        self.assertEqual(1, len(filed), filed)
        self.assertIn("suite had passed", filed[0])

    def test_the_list_says_what_it_holds_back_beside_what_it_would_send(self):
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.captured("the evidence gate refused a turn whose suite had passed")
        out = self.report().stdout
        self.assertIn("suite had passed", out)
        self.assertIn("1 held back: last seen on 2.2.0", out)
        self.assertNotIn("founder named", out, "a held report's body is not offered for sending")

    def test_a_defect_seen_again_on_this_version_is_sent(self):
        """A sighting stamps the version: the old report is this version's now."""
        said = "the merge gate refused a pull request the founder named"
        self.captured_on("2.2.0", said)
        self.captured(said)
        sendable = defects.unsent(self.ctx())
        self.assertEqual(1, len(sendable))
        self.assertEqual(2, sendable[0]["seen"])

    def test_the_founders_line_counts_only_what_would_be_sent(self):
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.assertEqual("", defects.line(self.ctx()))

    def test_a_report_written_with_no_version_is_held_back(self):
        store.append_jsonl(store.tier_b(self.ctx(), defects.DEFECTS_FILE), {
            "signature": "s", "kind": "observed", "gate": "pre-tool",
            "error": "a report from before reports carried their version",
        })
        self.assertEqual([], defects.unsent(self.ctx()))
        self.assertIn("1 held back: last seen on ?,", self.report().stdout)


class TestNothingIsSentFromAVersionAReleasePassed(SeenOnVersionsCase):
    def test_a_newer_release_holds_every_report_and_names_the_update(self):
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.released("v99.0.0")
        proc = self.report("send")
        self.assertEqual(1, proc.returncode)
        self.assertEqual([], self.issues_filed())
        self.assertIn("99.0.0", proc.stderr)
        self.assertIn("claude plugin update", proc.stderr)
        self.assertEqual(1, len(defects.unsent(self.ctx())), "held, not marked sent")

    def test_an_older_or_equal_release_does_not_hold_anything(self):
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.released("v1.0.0")
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(self.issues_filed()))

    def test_no_answer_about_releases_holds_nothing(self):
        """Offline, or `gh` failing for this call: sending never waits on the question."""
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.released("")
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(self.issues_filed()))

    def test_a_failed_question_is_no_answer_whatever_it_printed(self):
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.released("v99.0.0", exit_code=1)
        proc = self.report("send")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(self.issues_filed()))

    def test_a_newer_version_installed_on_disk_holds_too(self):
        self.captured("the evidence gate refused a turn whose suite had passed")
        cache = Path(os.environ["HOME"]) / ".claude" / "plugins" / "cache" / "m" / "bestpractice"
        (cache / "99.1.0").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, cache.parent)
        proc = self.report("send")
        self.assertEqual(1, proc.returncode)
        self.assertIn("99.1.0", proc.stderr)
        self.assertEqual([], self.issues_filed())

    def test_nothing_to_send_asks_nobody(self):
        """No report sendable, no `gh` call: the network is asked only to decide a send."""
        self.captured_on("2.2.0", "the merge gate refused a pull request the founder named")
        self.report("send")
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
