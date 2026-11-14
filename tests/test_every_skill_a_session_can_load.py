"""Every skill a session can load is listed, counted, and missed when it goes (#364).

A founder's repository had three synced skills gone for four weeks after a claude.ai account
change, and its two most important skills read 17 and 2 times by file with no Skill-tool call,
which no counter saw. The plugin counted its own three skills and nothing else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from helpers import BIN

from claude_bestpractice import skills, skilluse, store

from test_gates import GateCase


def skill(folder: Path, name: str) -> Path:
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: test\n---\n\nbody\n")
    return path


def synced(home: Path, account: str, names: list[str], days_ago: float) -> Path:
    folder = home / ".claude" / "skills" / "synced" / account
    for name in names:
        skill(folder, name)
    (folder / "manifest.json").write_text(json.dumps({
        "lastUpdated": int((time.time() - days_ago * 86400) * 1000),
        "skills": [{"skillId": name, "name": name, "source": "user"} for name in names],
    }))
    return folder


class SkillsCase(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        unset = mock.patch.dict(os.environ, {"CLAUDE_CODE_PLUGIN_CACHE_DIR": ""})
        unset.start()
        self.addCleanup(unset.stop)

    def names(self) -> dict[str, str]:
        return {item.name: item.kind for item in skills.inventory(self.repo, self.home)}


class TestEverySourceIsListed(SkillsCase):
    def test_the_repositorys_own_skills_and_commands(self):
        skill(self.repo / ".claude" / "skills", "scraper")
        commands = self.repo / ".claude" / "commands"
        (commands / "frontend").mkdir(parents=True)
        (commands / "stack.md").write_text("land a stack\n")
        (commands / "frontend" / "check.md").write_text("check\n")
        listed = self.names()
        self.assertEqual("project", listed["scraper"])
        self.assertEqual("project", listed["stack"])
        self.assertEqual("project", listed["frontend:check"])

    def test_a_directory_scoped_skill_git_tracks(self):
        skill(self.repo / "apps" / "web" / ".claude" / "skills", "deploy")
        subprocess.run(["git", "add", "apps"], cwd=self.repo, check=True)
        self.assertEqual("project", self.names()["apps/web:deploy"])

    def test_the_founders_own_and_none_of_the_synced_folder(self):
        skill(self.home / ".claude" / "skills", "notes")
        synced(self.home, "org_a", ["design"], days_ago=0)
        listed = self.names()
        self.assertEqual("user", listed["notes"])
        self.assertNotIn("synced", listed)

    def test_only_the_account_synced_last_loads(self):
        synced(self.home, "org_old", ["design", "seo", "pdf"], days_ago=29)
        synced(self.home, "org_new", ["pdf"], days_ago=0)
        listed = self.names()
        self.assertEqual("synced", listed["anthropic-skills:pdf"])
        self.assertNotIn("anthropic-skills:design", listed)

    def test_a_plugin_the_settings_enable_from_its_newest_copy(self):
        cache = self.home / ".claude" / "plugins" / "cache" / "market" / "kit"
        old, new = cache / "1.0.0", cache / "1.1.0"
        skill(old / "skills", "retired")
        (old / ".orphaned_at").write_text("1")
        skill(new / "skills", "review")
        (new / "commands").mkdir()
        (new / "commands" / "ship.md").write_text("ship\n")
        skill(self.home / ".claude" / "plugins" / "cache" / "market" / "off" / "1.0.0" / "skills",
              "hidden")
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "settings.json").write_text(json.dumps(
            {"enabledPlugins": {"kit@market": True, "off@market": False}}))
        listed = self.names()
        self.assertEqual("plugin", listed["kit:review"])
        self.assertEqual("plugin", listed["kit:ship"])
        self.assertNotIn("kit:retired", listed)
        self.assertNotIn("off:hidden", listed)


class TestAnAccountLeftBehind(SkillsCase):
    def test_its_skills_the_current_account_lacks_are_named(self):
        synced(self.home, "org_old", ["design", "seo", "pdf"], days_ago=29)
        synced(self.home, "org_new", ["pdf"], days_ago=0)
        behind = skills.left_behind(self.home)
        self.assertEqual(1, len(behind))
        self.assertEqual(("design", "seo"), behind[0].names)

    def test_one_account_leaves_nothing_behind(self):
        synced(self.home, "org_a", ["pdf"], days_ago=0)
        self.assertEqual([], skills.left_behind(self.home))


class TestEverySkillIsCounted(SkillsCase):
    def test_each_by_the_name_a_session_invokes_it_by(self):
        self.start()
        skilluse.used(self.ctx(), "llm-first-code", plugin="bestpractice", source="plugin")
        skilluse.used(self.ctx(), "review", plugin="kit", source="plugin")
        skilluse.used(self.ctx(), "pdf", source="syncedSkills")
        skilluse.used(self.ctx(), "scraper", source="projectSettings")
        counted = skilluse.counts(self.ctx())
        self.assertEqual((1, 0), counted["llm-first-code"])
        self.assertEqual((1, 0), counted["kit:review"])
        self.assertEqual((1, 0), counted["anthropic-skills:pdf"])
        self.assertEqual((1, 0), counted["scraper"])

    def test_a_read_of_the_file_counts_for_the_skill_it_is(self):
        self.start()
        path = skill(self.repo / ".claude" / "skills", "scraper")
        for _ in range(3):
            skilluse.read_directly(self.ctx(), str(path))
        self.assertEqual((0, 3), skilluse.counts(self.ctx())["scraper"])

    def test_the_module_reports_a_read_through_the_bridge(self):
        self.start()
        path = skill(self.repo / ".claude" / "skills", "scraper")
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-mod"), "skill-read"],
                              input=json.dumps({"cwd": str(self.repo), "session": "s1",
                                                "path": str(path)}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual((0, 1), skilluse.counts(self.ctx())["scraper"])


class TestASkillThatGoesIsMissed(SkillsCase):
    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.path = skill(self.repo / ".claude" / "skills", "scraper")
        skilluse.used(self.ctx(), "scraper", source="projectSettings", path=str(self.path))

    def test_one_still_listed_is_not_gone(self):
        self.assertEqual([], skilluse.vanished(self.ctx(), skills.inventory(self.repo, self.home)))

    def test_one_no_source_lists_is_named_with_where_it_was(self):
        self.path.unlink()
        gone = skilluse.vanished(self.ctx(), skills.inventory(self.repo, self.home))
        self.assertEqual([("scraper", f"last loaded from {self.path}")], gone)

    def test_the_board_says_it_once(self):
        self.path.unlink()
        first = skilluse.notice(self.ctx(), self.home)
        self.assertIn("scraper", first)
        self.assertEqual("", skilluse.notice(self.ctx(), self.home))

    def test_the_board_names_an_accounts_skills_left_behind(self):
        synced(self.home, "org_old", ["design"], days_ago=29)
        synced(self.home, "org_new", ["pdf"], days_ago=0)
        self.assertIn("design (synced from a claude.ai account until", skilluse.notice(self.ctx(), self.home))

    def test_status_lists_what_loads_what_was_read_and_what_went(self):
        skill(self.repo / ".claude" / "skills", "unused")
        self.path.unlink()
        lines = skilluse.report(self.ctx(), self.home)
        self.assertTrue(lines[0].startswith("1 loadable: 1 project"), lines)
        self.assertTrue(any(line.startswith("read since") and "scraper 1" in line for line in lines))
        self.assertIn("never read: unused", lines)
        self.assertTrue(any(line.startswith("gone: scraper") for line in lines), lines)

    def test_a_reindex_keeps_what_was_told(self):
        """Told once is a record of an event, which a rescan of the tree does not bring back."""
        self.path.unlink()
        skilluse.notice(self.ctx(), self.home)
        store.purge_tier_b(self.ctx())
        self.assertEqual("", skilluse.notice(self.ctx(), self.home))


class TestASkillThisPluginMovedIntoAPack(SkillsCase):
    """`landing-not-slop` left the plugin for the `design` pack: a repository that read it is
    told where it went and the one command that brings it back, once."""

    def setUp(self) -> None:
        super().setUp()
        self.start()
        skilluse.used(self.ctx(), "landing-not-slop", plugin="bestpractice", source="plugin")

    def test_it_is_named_with_the_command_that_brings_it_back(self):
        said = skilluse.notice(self.ctx(), self.home)
        self.assertIn("landing-not-slop (moved to the `design` pack: `claude plugin install "
                      "curated@claude-bestpractice` brings every pack)", said)

    def test_with_the_pack_enabled_by_curated_at_user_scope_nothing_is_gone(self):
        """The CLI writes a dependency into the user's `enabledPlugins` (2.1.325)."""
        pack = self.home / ".claude" / "plugins" / "cache" / "claude-bestpractice" / "design"
        skill(pack / "1.0.1" / "skills", "landing-not-slop")
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {
            "curated@claude-bestpractice": True, "design@claude-bestpractice": True}}))
        self.assertEqual([], skilluse.vanished(self.ctx(), skills.inventory(self.repo, self.home)))

    def test_with_the_pack_enabled_here_nothing_is_gone(self):
        pack = self.home / ".claude" / "plugins" / "cache" / "claude-bestpractice" / "design"
        skill(pack / "1.0.0" / "skills", "landing-not-slop")
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "settings.json").write_text(json.dumps(
            {"enabledPlugins": {"design@claude-bestpractice": True}}))
        self.assertEqual([], skilluse.vanished(self.ctx(), skills.inventory(self.repo, self.home)))
