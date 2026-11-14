"""How often this plugin's skills are read (decision 0080).

A skill loads only when the model chooses it, and its description rides in every request either
way. Ponytail's skill, tested alone, was chosen in none of ten sessions. The count starts at a
session start, so a skill nobody reads shows as read none.
"""

from __future__ import annotations

import json
import subprocess
import sys

from helpers import BIN

from claude_bestpractice import skilluse, store

from test_gates import GateCase


class TestTheCount(GateCase):
    def test_a_session_start_begins_it_and_a_skill_never_read_shows_none(self):
        self.start()
        said = skilluse.line(self.ctx())
        for name in skilluse.ours():
            self.assertIn(f"{name} 0", said)

    def test_a_read_of_ours_counts_by_its_listed_name_or_its_bare_one(self):
        self.start()
        skilluse.used(self.ctx(), "bestpractice:llm-first-code")
        skilluse.used(self.ctx(), "llm-first-code")
        self.assertIn("llm-first-code 2", skilluse.line(self.ctx()))

    def test_another_plugins_skill_is_counted_as_its_own_not_as_ours(self):
        """Every skill is counted since #364; this plugin's line still names only its own."""
        self.start()
        skilluse.used(self.ctx(), "commit", plugin="kit")
        self.assertNotIn("commit", skilluse.line(self.ctx()))
        self.assertEqual((1, 0), skilluse.counts(self.ctx())["kit:commit"])

    def test_the_module_counts_through_the_bridge(self):
        self.start()
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-mod"), "skill"],
                              input=json.dumps({"cwd": str(self.repo), "session": "s1",
                                                "skill": "founder-defaults"}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("founder-defaults 1", skilluse.line(self.ctx()))

    def test_a_reindex_keeps_the_count(self):
        """A count is a record of events, which no rescan brings back (`store.CARRIED`)."""
        self.start()
        skilluse.used(self.ctx(), "llm-first-code")
        store.purge_tier_b(self.ctx())
        self.assertIn("llm-first-code 1", skilluse.line(self.ctx()))

    def test_before_any_session_started_status_says_nothing(self):
        self.assertEqual("", skilluse.line(self.ctx()))
