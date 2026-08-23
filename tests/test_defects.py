"""Defect capture — the plugin reporting its own failures without costing a turn."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, LIB, RepoCase

from claude_bestpractice import defects


class TestNothingFromTheRepositoryLeaves(unittest.TestCase):
    """A report is filed publicly, under the installer's name, in someone else's repo.

    So the only defensible content is the plugin's own: the gate, the exception, the line.
    An early version kept the basename of foreign paths and put `billing.py` — a filename
    out of a private repository — into an issue whose own last line promised "nothing from
    the repository it ran in". The claim was made true rather than softened.
    """

    def test_a_credential_in_an_error_never_reaches_the_report(self):
        dirty = "KeyError: token=sk-ant-abcdefghijklmnopqrstuvwxyz012345 rejected"
        clean = defects.sanitize(dirty)
        self.assertNotIn("sk-ant-abcdefghij", clean)
        self.assertIn("REDACTED", clean)

    def test_the_founders_tree_never_reaches_the_report(self):
        clean = defects.sanitize("FileNotFoundError: /home/anna/clients/acme/src/billing.py")
        for secret in ("anna", "clients", "acme", "billing.py"):
            self.assertNotIn(secret, clean, clean)

    def test_the_plugins_own_files_are_kept_whole(self):
        """Erasing ours too would leave a report that says nothing actionable."""
        clean = defects.sanitize("at /opt/x/claude_bestpractice/evidence.py line 40")
        self.assertIn("claude_bestpractice/evidence.py", clean)

    def test_the_body_says_only_what_it_carries(self):
        report = {"gate": "evidence-gate", "error": "KeyError: x", "where": "a.py:1",
                  "version": "9.9.9", "python": "3.11", "platform": "linux", "seen": 1}
        body = defects.body(report)
        self.assertIn("evidence-gate", body)
        self.assertIn("9.9.9", body)
        self.assertLess(len(body), 600, "a long machine report is one nobody reads")


class TestACrashIsCapturedNotNarrated(RepoCase):
    """Capture must cost the session nothing: written to disk, injected nowhere.

    A crash report the agent has to read is worse than the crash — it spends the founder's
    context on the plugin's problems instead of their work.
    """

    def crash_a_gate(self, message: str = "broken", exception: str = "KeyError"):
        """Drive `hookio.guard` through a real failure, as a real gate would."""
        from helpers import LIB

        script = (
            # The real path, because that is what the harness passes and what decides
            # whether a crash is one of ours at all. The bare name this used to set made
            # the fixture pass a test the gates themselves would fail.
            f"import sys, os; sys.path.insert(0, {str(LIB)!r});\n"
            f"sys.argv[0] = {str(BIN / 'evidence-gate')!r}\n"
            f"os.chdir({str(self.repo)!r})\n"
            "from claude_bestpractice import hookio\n"
            f"def boom(): raise {exception}({message!r})\n"
            "hookio.guard(boom, fail_closed=False)\n"
        )
        return subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            cwd=str(self.repo), timeout=60,
        )

    def test_a_crashed_gate_is_recorded(self):
        self.crash_a_gate()
        captured = defects.unsent(self.ctx())
        self.assertEqual(1, len(captured))
        self.assertEqual("evidence-gate", captured[0]["gate"])
        self.assertIn("KeyError", captured[0]["error"])

    def test_a_crash_in_something_that_is_not_ours_is_not_captured(self):
        """`guard` is reached by importing the library, so a test suite, a REPL or any
        `python3 -m …` that raises lands in the same handler. Ninety-four rows of this
        plugin's own `RuntimeError: kaboom` were sitting in a real repository, behind a
        command whose whole job is to file them at GitHub."""
        script = (
            f"import sys, os; sys.path.insert(0, {str(LIB)!r})\n"
            f"os.chdir({str(self.repo)!r})\n"
            "from claude_bestpractice import hookio\n"
            "def boom(): raise RuntimeError('kaboom')\n"
            "hookio.guard(boom, fail_closed=False)\n"
        )
        subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                       cwd=str(self.repo), timeout=60)
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_nothing_is_written_to_the_session(self):
        """stdout is the hook's response to the harness; a report there would be context."""
        proc = self.crash_a_gate()
        self.assertNotIn("KeyError", proc.stdout)
        self.assertNotIn("defect", proc.stdout.lower())

    def test_the_same_crash_twice_is_one_report(self):
        """A gate stuck in a loop must not fill the disk or the founder's attention."""
        for _ in range(5):
            self.crash_a_gate()
        captured = defects.unsent(self.ctx())
        self.assertEqual(1, len(captured))
        self.assertEqual(5, captured[0]["seen"])

    def test_the_message_does_not_split_one_defect_into_many(self):
        """`KeyError: 'a'` and `KeyError: 'b'` at one line are one defect in one place.

        Keying on the message would file a fresh report for every value the data happened
        to take, which is how a looping gate becomes fifty issues in a stranger's repo.
        """
        self.crash_a_gate("artifact_globs")
        self.crash_a_gate("test_command")
        captured = defects.unsent(self.ctx())
        self.assertEqual(1, len(captured))
        self.assertEqual(2, captured[0]["seen"])

    def test_a_different_failure_is_a_different_defect(self):
        """Grouping by type must not collapse two genuinely separate defects into one."""
        self.crash_a_gate()
        self.crash_a_gate("bad glob", exception="ValueError")
        self.assertEqual(2, len(defects.unsent(self.ctx())))

    def test_capture_can_be_switched_off(self):
        self.configure(report_defects="off")
        self.crash_a_gate()
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_a_report_is_not_sent_by_capturing_it(self):
        """The default holds. Filing uses the installer's credentials and posts publicly
        under their name in a repository they do not own; that is not a hook's call."""
        self.crash_a_gate()
        self.assertEqual(1, len(defects.unsent(self.ctx())))
        self.assertFalse(defects.load(self.ctx())[0].get("sent_at"))

    def test_the_founder_is_told_once_it_has_something_to_say(self):
        self.assertEqual("", defects.line(self.ctx()))
        self.crash_a_gate()
        self.assertIn("1 plugin defect", defects.line(self.ctx()))

    def test_sending_marks_them_so_nothing_is_filed_twice(self):
        """A report filed twice is noise in somebody else's repository."""
        self.crash_a_gate()
        report = defects.unsent(self.ctx())[0]
        defects.mark_sent(self.ctx(), report, "https://example.invalid/1")
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_the_cli_shows_exactly_what_would_be_sent(self):
        self.crash_a_gate()
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-report")],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("evidence-gate", proc.stdout)
        self.assertIn(defects.REPORT_REPO, proc.stdout)


class TestAKnownBadReleaseSaysSo(unittest.TestCase):
    """A released version cannot be withdrawn, so the copy that is running has to say it.

    The tag is permanent and `claude plugin` keeps serving it, so an old release looks fine
    from the outside — which is how somebody stays on a version that cannot push.
    """

    def test_a_listed_version_is_named_with_a_reason(self):
        from claude_bestpractice import upgrade

        warning = upgrade.known_bad("1.0.13")
        self.assertIn("1.0.13", warning)
        self.assertIn("virtualenv", warning)
        self.assertIn("claude plugin update", warning)

    def test_an_unlisted_version_costs_nothing(self):
        from claude_bestpractice import upgrade

        self.assertEqual("", upgrade.known_bad("99.0.0"))

    def test_the_current_version_is_not_on_the_list(self):
        """Shipping a release that declares itself broken would be a strange thing to do."""
        from claude_bestpractice import __version__, upgrade

        self.assertEqual("", upgrade.known_bad(__version__))


if __name__ == "__main__":
    unittest.main()
