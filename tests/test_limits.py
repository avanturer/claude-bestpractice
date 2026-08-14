"""What the account has left, carried from where it is shown to where it is decided.

Claude Code hands the five-hour and weekly usage to exactly one consumer — the `statusLine`
command, on stdin. Hooks never receive it, there is no `usage` subcommand, and nothing on
disk holds it; all four were checked before this was built. So the status line is the
bridge, and these tests are about it not lying.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import limits, store


def payload(five=23.5, week=41.2, five_in=7860, week_in=273_600, **extra):
    now = int(time.time())
    return {
        "rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": now + five_in},
            "seven_day": {"used_percentage": week, "resets_at": now + week_in},
        },
        **extra,
    }


class TestWhatTheStatusLineCarries(RepoCase):
    def test_the_numbers_reach_the_board(self):
        limits.record(self.ctx(), payload())
        said = limits.line(self.ctx())
        self.assertIn("5h 24%", said)
        self.assertIn("week 41%", said)

    def test_the_time_until_reset_is_shown(self):
        limits.record(self.ctx(), payload(five_in=7860, week_in=273_600))
        said = limits.line(self.ctx())
        self.assertIn("resets in 2h1", said)
        self.assertIn("resets in 3d", said)

    def test_it_says_where_the_live_number_is(self):
        """The board is injected once at session start (decision 0003), and the status line
        rewrites the file continuously — so an eleven-hour session holds an eleven-hour-old
        percentage unless it goes and looks. Knowing where is what makes looking possible."""
        limits.record(self.ctx(), payload())
        said = limits.line(self.ctx())
        self.assertIn("as at session start", said)
        self.assertIn(str(store.tier_b(self.ctx(), limits.FILE)), said)

    def test_nothing_is_said_before_a_status_line_has_ever_run(self):
        self.assertEqual("", limits.line(self.ctx()))

    def test_a_stale_number_is_not_shown_at_all(self):
        """Usage moves. A percentage from this morning reads as current and is worse than
        none — and six hours outlives the five-hour window itself, so nothing survives a
        reset."""
        limits.record(self.ctx(), payload())
        old = time.time() + limits.MAX_AGE_SECONDS + 60
        self.assertEqual("", limits.line(self.ctx(), now=old))

    def test_a_payload_with_no_limits_records_nothing(self):
        """Every status line render calls this, and most of them carry no rate limits."""
        self.assertEqual({}, limits.record(self.ctx(), {"model": {"display_name": "Opus"}}))
        self.assertEqual("", limits.line(self.ctx()))

    def test_a_reset_already_past_is_not_counted_down(self):
        limits.record(self.ctx(), payload(five_in=-10, week_in=-10))
        said = limits.line(self.ctx())
        self.assertIn("5h", said)
        self.assertNotIn("resets in", said)

    def test_one_window_missing_does_not_lose_the_other(self):
        limits.record(self.ctx(), {"rate_limits": {"five_hour": {"used_percentage": 12}}})
        said = limits.line(self.ctx())
        self.assertIn("5h 12%", said)
        self.assertNotIn("week", said)


class TestTheStatusLineItself(RepoCase):
    def run_bar(self, body: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-statusline")],
            input=json.dumps(body), capture_output=True, text=True,
            cwd=str(self.repo), timeout=120,
        )

    def test_it_prints_a_line_and_records_the_limits(self):
        proc = self.run_bar(payload(cwd=str(self.repo), model={"display_name": "Opus"}))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("Opus", proc.stdout)
        self.assertIn("5h 24%", proc.stdout)
        self.assertNotEqual("", limits.line(self.ctx()))

    def test_it_still_prints_when_there_is_nothing_to_record(self):
        """A status line that fails is one the founder turns off, and the bridge goes with
        it."""
        proc = self.run_bar({"cwd": str(self.repo)})
        self.assertEqual(0, proc.returncode)
        self.assertTrue(proc.stdout.strip())

    def test_unparseable_input_is_not_a_crash(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-statusline")],
            input="this is not json", capture_output=True, text=True,
            cwd=str(self.repo), timeout=120,
        )
        self.assertEqual(0, proc.returncode)
        self.assertTrue(proc.stdout.strip())


class TestInstallingItNeverTakesOverTheirs(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({"autoMode": {"allow": ["a rule they wrote"]}}))

    def read(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_it_installs_when_nothing_is_there(self):
        ok, found = limits.install("/plugin/bin/claude-bp-statusline", self.home)
        self.assertTrue(ok)
        self.assertEqual("/plugin/bin/claude-bp-statusline", self.read()["statusLine"]["command"])

    def test_their_own_status_line_is_never_replaced(self):
        """A status bar is their display, not this plugin's state — the opposite of the
        `policy` rule, and the reason is that taking it over is how a tool gets uninstalled."""
        current = self.read()
        current["statusLine"] = {"type": "command", "command": "~/my-own-bar.sh"}
        self.settings.write_text(json.dumps(current))

        ok, found = limits.install("/plugin/bin/claude-bp-statusline", self.home)
        self.assertFalse(ok)
        self.assertEqual("~/my-own-bar.sh", found)
        self.assertEqual("~/my-own-bar.sh", self.read()["statusLine"]["command"])

    def test_everything_else_in_their_settings_survives(self):
        limits.install("/plugin/bin/claude-bp-statusline", self.home)
        self.assertEqual(["a rule they wrote"], self.read()["autoMode"]["allow"])

    def test_installing_twice_is_not_a_second_write(self):
        limits.install("/plugin/bin/claude-bp-statusline", self.home)
        ok, found = limits.install("/plugin/bin/claude-bp-statusline", self.home)
        self.assertTrue(ok)
        self.assertEqual("/plugin/bin/claude-bp-statusline", found)


if __name__ == "__main__":
    unittest.main()
