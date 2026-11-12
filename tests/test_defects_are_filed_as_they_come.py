"""Where `gh` files as the owner of the reports' repository, a report is filed when captured.

Held for a `send` nobody ran, fifty reports piled up for a month and went in one burst, most of
them about defects fixed long before (#313–#362). Holding is for an installer who does not own
the repository the reports go to: filing posts publicly under their name. The owner owns both
ends, and is the one who wants the defect in front of a fix the moment it happens.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from helpers import BIN, LIB
from test_defects_seen_on_this_version import SeenOnVersionsCase

from claude_bestpractice import defects

OWNER = defects.REPORT_REPO.split("/", 1)[0]

MULTI_ACCOUNT = f"""\
github.com:
    users:
        someone-else:
            user: someone-else
        {OWNER}:
    git_protocol: https
    user: {{active}}
ghe.example.com:
    user: {OWNER}
"""


class FiledAsTheyComeCase(SeenOnVersionsCase):
    def setUp(self) -> None:
        super().setUp()
        self.gh_config = self.tmp / "gh-config"
        self.gh_config.mkdir()
        patched = mock.patch.dict(os.environ, {"GH_CONFIG_DIR": str(self.gh_config)})
        patched.start()
        self.addCleanup(patched.stop)

    def gh_files_as(self, account: str) -> None:
        (self.gh_config / "hosts.yml").write_text(MULTI_ACCOUNT.format(active=account))

    def filed_within(self, count: int, seconds: float = 20.0) -> list[str]:
        """The sender is detached: wait for what it files, no longer than it could take."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and len(self.issues_filed()) < count:
            time.sleep(0.1)
        return self.issues_filed()


class TestTheOwnerIsWhoReportsAreSentFor(FiledAsTheyComeCase):
    def test_gh_filing_as_the_owner_sends_them(self):
        self.gh_files_as(OWNER)
        self.assertEqual(defects.AUTO, defects.mode(self.ctx()))

    def test_the_owners_name_in_another_case_is_the_owner(self):
        self.gh_files_as(OWNER.upper())
        self.assertEqual(defects.AUTO, defects.mode(self.ctx()))

    def test_anyone_else_holds_them(self):
        self.gh_files_as("someone-else")
        self.assertEqual(defects.LOCAL, defects.mode(self.ctx()))

    def test_no_gh_config_holds_them(self):
        self.assertEqual(defects.LOCAL, defects.mode(self.ctx()))

    def test_an_account_on_another_host_or_merely_logged_in_is_not_the_one_filing(self):
        (self.gh_config / "hosts.yml").write_text(
            f"ghe.example.com:\n    user: {OWNER}\n"
            f"github.com:\n    users:\n        {OWNER}:\n            user: {OWNER}\n"
            f"    user: someone-else\n"
        )
        self.assertEqual("someone-else", defects.gh_account())

    def test_a_token_in_the_environment_decides_the_account_so_nothing_is_known(self):
        self.gh_files_as(OWNER)
        with mock.patch.dict(os.environ, {"GH_TOKEN": "a-token-of-somebody"}):
            self.assertEqual(defects.LOCAL, defects.mode(self.ctx()))

    def test_the_founders_written_word_wins(self):
        self.gh_files_as(OWNER)
        self.configure(report_defects="local")
        self.assertEqual(defects.LOCAL, defects.mode(self.ctx()))
        self.configure(report_defects="off")
        self.assertEqual(defects.OFF, defects.mode(self.ctx()))
        self.gh_files_as("someone-else")
        self.configure(report_defects="auto")
        self.assertEqual(defects.AUTO, defects.mode(self.ctx()))


class TestAReportIsFiledWhenItIsCaptured(FiledAsTheyComeCase):
    def test_a_sessions_report_is_filed_at_once_and_it_is_told_so(self):
        self.gh_files_as(OWNER)
        proc = self.report("defect", "the merge gate refused a pull request the founder named")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(f"being filed at {defects.REPORT_REPO}", proc.stdout)
        filed = self.filed_within(1)
        self.assertEqual(1, len(filed), filed)
        self.assertIn("founder named", filed[0])
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_a_crashed_gate_is_filed_at_once(self):
        self.gh_files_as(OWNER)
        script = (
            f"import sys, os; sys.path.insert(0, {str(LIB)!r})\n"
            f"sys.argv[0] = {str(BIN / 'evidence-gate')!r}\n"
            f"os.chdir({str(self.repo)!r})\n"
            "from claude_bestpractice import hookio\n"
            "def boom(): raise KeyError('artifact_globs')\n"
            "hookio.guard(boom, fail_closed=False)\n"
        )
        subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                       cwd=str(self.repo), timeout=60)
        filed = self.filed_within(1)
        self.assertEqual(1, len(filed), filed)
        self.assertIn("evidence-gate crashed", filed[0])

    def test_where_reports_are_held_capture_starts_no_sender(self):
        self.gh_files_as("someone-else")
        with mock.patch.object(defects, "send_soon") as sender:
            recorded, said = defects.observed(self.ctx(), "the gate refused its own remedy",
                                              "pre-tool")
        self.assertTrue(recorded)
        sender.assert_not_called()
        self.assertIn("`claude-bp-report` shows what would be sent", said)

    def test_what_a_capture_could_not_file_goes_at_the_next_session_start(self):
        self.captured("the evidence gate refused a turn whose suite had passed")
        self.gh_files_as(OWNER)
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                        "source": "startup"})
        self.assertEqual(1, len(self.filed_within(1)))

    def test_two_senders_at_once_file_each_report_once(self):
        for n in range(3):
            self.captured(f"report {n}: the gate refused something it should have allowed")
        slow = self.stubs / "gh"
        slow.write_text(slow.read_text().replace(
            '"issue create") echo', '"issue create") sleep 0.3; echo'))
        senders = [subprocess.Popen([sys.executable, str(BIN / "claude-bp-report"), "send"],
                                    cwd=str(self.repo), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL) for _ in range(2)]
        for sender in senders:
            sender.wait(timeout=60)
        self.assertEqual(3, len(self.issues_filed()), self.issues_filed())


if __name__ == "__main__":
    import unittest

    unittest.main()
