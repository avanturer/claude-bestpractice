"""Who holds the pen on the permission policy.

Two layers answer "may this call proceed unattended" — the classifier from prose in the
founder's settings, this plugin from state it computes — and the only thing joining them
was the founder retyping one half into the other, mid-session, at the moment a prompt had
already interrupted something else. 8,940 bytes of it on one machine (#113).

The tests are split the way the risk is: what may be generated, what may never be, and
that nothing hand-written is touched by either.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import config, knowledge, policy

HAND_WRITTEN = {
    "permissions": {
        "allow": [
            "Bash(git worktree add:*)",
            "EnterWorktree",
            "Bash(eas build:*)",
        ]
    },
    "autoMode": {
        "allow": ["merging a pull request the agent authored is expected work here"],
        "soft_deny": ["direct push to main"],
        "environment": ["the production ssh key lives in the repository checkout"],
    },
}


class PolicyCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.settings = self.home / ".claude" / "settings.json"

    def hand_written(self) -> None:
        self.settings.write_text(json.dumps(HAND_WRITTEN), encoding="utf-8")

    def read(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def checks(self) -> list:
        return config.load(self.ctx()).test_command


class TestOnlyFactsAreGenerated(PolicyCase):
    def test_every_generated_entry_carries_this_repositorys_marker(self):
        for entry in policy.facts(self.ctx(), ["make", "test"]):
            self.assertTrue(entry.startswith(policy.marker(self.ctx())), entry[:60])

    def test_the_facts_are_about_this_repository(self):
        git(["remote", "add", "origin", "https://github.com/o/r.git"], self.repo)
        joined = " ".join(policy.facts(self.ctx(), ["make", "test"]))
        self.assertIn(self.repo.as_posix(), joined)
        self.assertIn("https://github.com/o/r.git", joined)
        self.assertIn("make test", joined)

    def test_a_fact_is_re_derived_rather_than_remembered(self):
        """The failure this prevents is a live rule stating that a production key lives in
        the checkout, a day after it was moved out and revoked on the server."""
        before = policy.facts(self.ctx(), ["make", "test"])
        git(["remote", "add", "origin", "https://github.com/o/moved.git"], self.repo)
        self.assertNotEqual(before, policy.facts(self.ctx(), ["make", "test"]))

    def test_no_grant_is_ever_written(self):
        """`autoMode.allow` widens what may proceed unattended, and a session that has just
        been interrupted has a direct motive to widen it."""
        self.hand_written()
        policy.apply(self.ctx(), self.checks(), self.home)
        after = self.read()
        self.assertEqual(HAND_WRITTEN["autoMode"]["allow"], after["autoMode"]["allow"])
        self.assertEqual(HAND_WRITTEN["autoMode"]["soft_deny"], after["autoMode"]["soft_deny"])
        self.assertEqual(HAND_WRITTEN["permissions"]["allow"], after["permissions"]["allow"])


class TestNothingHandWrittenIsTouched(PolicyCase):
    def test_the_founders_own_environment_prose_survives_verbatim(self):
        self.hand_written()
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertIn(
            HAND_WRITTEN["autoMode"]["environment"][0],
            self.read()["autoMode"]["environment"],
        )

    def test_another_repositorys_block_is_left_alone(self):
        """This file governs every repository on the machine while the plugin is installed
        per project, so refreshing one must never drop another's."""
        self.hand_written()
        theirs = "[claude-bestpractice /somewhere/else] their repository is elsewhere"
        current = self.read()
        current["autoMode"]["environment"].append(theirs)
        self.settings.write_text(json.dumps(current), encoding="utf-8")

        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertIn(theirs, self.read()["autoMode"]["environment"])

    def test_keys_this_module_has_never_heard_of_are_carried_through(self):
        self.settings.write_text(json.dumps({"enabledPlugins": {"x": True}}), encoding="utf-8")
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertEqual({"x": True}, self.read()["enabledPlugins"])

    def test_a_dead_rule_is_named_and_not_removed(self):
        """A rule that is redundant HERE may be why something works in another repository,
        and this module cannot see that one."""
        self.hand_written()
        found = policy.apply(self.ctx(), self.checks(), self.home)
        self.assertTrue(any("git worktree" in entry for entry in found.dead))
        self.assertTrue(any("EnterWorktree" in entry for entry in found.dead))
        self.assertEqual(HAND_WRITTEN["permissions"]["allow"], self.read()["permissions"]["allow"])

    def test_a_rule_the_gate_does_not_answer_is_not_called_dead(self):
        self.hand_written()
        found = policy.delta(self.ctx(), self.checks(), self.home)
        self.assertFalse(any("eas build" in entry for entry in found.dead), found.dead)


class TestItConverges(PolicyCase):
    def test_applying_twice_changes_nothing_the_second_time(self):
        self.hand_written()
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertTrue(policy.delta(self.ctx(), self.checks(), self.home).in_sync)
        self.assertEqual([], policy.apply(self.ctx(), self.checks(), self.home).add)

    def test_a_fact_that_stopped_being_true_is_dropped_not_stacked(self):
        policy.apply(self.ctx(), ["make", "test"], self.home)
        policy.apply(self.ctx(), ["pytest", "-q"], self.home)
        mine = [e for e in self.read()["autoMode"]["environment"]
                if e.startswith(policy.marker(self.ctx()))]
        self.assertEqual(len(policy.facts(self.ctx(), ["pytest", "-q"])), len(mine))
        self.assertFalse(any("make test" in entry for entry in mine))

    def test_a_missing_settings_file_is_created_rather_than_a_crash(self):
        self.assertFalse(self.settings.exists(), "the fixture proves nothing")
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertTrue(self.settings.exists())

    def test_an_unreadable_settings_file_is_not_overwritten_blind(self):
        """Whatever is in there, it is the founder's, and half-parsed is not permission."""
        self.settings.write_text("{ not json", encoding="utf-8")
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertIn("autoMode", self.read())

    def test_the_board_says_nothing_once_it_is_current(self):
        self.assertNotEqual("", policy.line(self.ctx(), self.checks(), self.home))
        policy.apply(self.ctx(), self.checks(), self.home)
        self.assertEqual("", policy.line(self.ctx(), self.checks(), self.home))

    def test_the_board_names_a_command_the_agent_runs(self):
        """A line telling the founder to go and edit a file would be the complaint restated."""
        said = policy.line(self.ctx(), self.checks(), self.home)
        self.assertIn("claude-bp policy --apply", said)
        self.assertIn("yourself", said)


class TestThroughTheCli(PolicyCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {"HOME": str(self.home), "PATH": __import__("os").environ.get("PATH", "")}
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "policy", *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=300, env=env,
        )

    def test_showing_writes_nothing(self):
        self.hand_written()
        self.assertEqual(0, self.cli().returncode)
        self.assertEqual(HAND_WRITTEN, self.read())

    def test_applying_writes_and_reports(self):
        self.hand_written()
        proc = self.cli("--apply")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("wrote:", proc.stdout)
        self.assertIn("NO LONGER DO ANYTHING", proc.stdout)
        mine = [e for e in self.read()["autoMode"]["environment"]
                if e.startswith(policy.marker(self.ctx()))]
        self.assertTrue(mine)


class TestAStandingRuleWhoseSubjectIsGone(RepoCase):
    """A rule file has no expiry and no test. One environment rule on a real machine still
    said the production key lived in the checkout, a day after it was moved out and revoked
    on the server — and nothing compared that claim to the world (#113)."""

    def rule(self, text: str) -> None:
        self.write(".claude/rules/security.md", text)

    def test_a_rule_naming_a_path_that_left_is_reported(self):
        self.rule("Always read the production key from ops/prod_key.pem before deploying.\n")
        found = knowledge.stale_rules(self.ctx())
        self.assertEqual(1, len(found), found)
        self.assertIn("ops/prod_key.pem", found[0])

    def test_a_rule_whose_subject_is_still_there_is_not(self):
        self.write("ops/prod_key.pem", "x\n")
        self.rule("Always read the production key from ops/prod_key.pem before deploying.\n")
        self.assertEqual([], knowledge.stale_rules(self.ctx()))

    def test_prose_with_no_subject_to_check_is_left_alone(self):
        """A rule about a concept has nothing to look for, and guessing is how a checker
        starts reporting the founder's own writing back at them."""
        self.rule("Always prefer the smaller change.\nNever guess at intent.\n")
        self.assertEqual([], knowledge.stale_rules(self.ctx()))

    def test_a_line_that_is_not_an_instruction_is_not_judged(self):
        self.rule("The importer used to live in old/importer.py.\n")
        self.assertEqual([], knowledge.stale_rules(self.ctx()))

    def test_a_url_is_not_a_path_in_this_repository(self):
        self.rule("Always open issues at github.com/o/r/issues before starting.\n")
        self.assertEqual([], knowledge.stale_rules(self.ctx()))

    def test_the_board_says_it_and_says_not_to_edit_the_file(self):
        self.rule("Always read the production key from ops/prod_key.pem before deploying.\n")
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("STANDING RULES OUT OF DATE", context)
        self.assertIn("do not edit their file", context)

    def test_the_board_is_silent_when_the_rules_match_the_tree(self):
        self.rule("Always prefer the smaller change.\n")
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("STANDING RULES OUT OF DATE", context)


if __name__ == "__main__":
    unittest.main()
