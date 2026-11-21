"""A skill's paths are looked for beside it too, and a copy of someone else's is theirs (#364).

`claude-bp skills` named eleven of seventeen findings in the founder's `~/.claude/skills` falsely:
seven scripts that sat in the skill's own `scripts/`, looked for in the repository alone, and four
`../` paths, whose first part `..` always exists. Each became a card in every repository. The
rest were steps of Anthropic's and an SEO toolkit's skills the founder had copied, licence and all.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from unittest import mock

from test_a_skill_followed_as_written import SkillCheckCase

from claude_bestpractice import migrate, plan, skillcheck, skills, skilluse, store

USES_ITS_OWN = """---
name: deck
description: make a deck
---

Run `scripts/thumbnail.py` on the result, and read `../shared/references/style.md`.

```bash
python3 scripts/inventory.py deck.pptx
python3 ooxml/scripts/pack.py out/ deck.pptx
```
"""

MISSES_ITS_OWN = """---
name: deck
description: make a deck
---

Read `../shared/references/gone.md`.

```bash
python3 scripts/missing.py deck.pptx
```
"""


class OwnFolderCase(SkillCheckCase):
    def user_skill(self, name: str, text: str, *files: str, licence: bool = False) -> Path:
        folder = self.tmp / "home" / ".claude" / "skills" / name
        (folder / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text)
        for rel in files:
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text("x\n")
        if licence:
            (folder / "LICENSE.txt").write_text("MIT\n")
        return folder

    def said(self) -> list[str]:
        return [f"{f.skill}:{f.line} {f.said}" for f in self.findings()]


class TestTheSkillsFolderIsLookedIn(OwnFolderCase):
    def test_scripts_and_paths_beside_the_skill_are_found(self):
        self.user_skill("deck", USES_ITS_OWN, "scripts/thumbnail.py", "scripts/inventory.py",
                        "ooxml/scripts/pack.py")
        shared = self.tmp / "home" / ".claude" / "skills" / "shared" / "references"
        shared.mkdir(parents=True)
        (shared / "style.md").write_text("style\n")
        self.assertEqual([], self.said())

    def test_what_is_in_neither_place_is_still_named(self):
        self.user_skill("deck", MISSES_ITS_OWN, "scripts/other.py")
        (self.tmp / "home" / ".claude" / "skills" / "shared").mkdir(parents=True)
        said = self.said()
        self.assertTrue(any("scripts/missing.py" in s and "skill's folder" in s for s in said), said)
        self.assertTrue(any("../shared/references/gone.md" in s for s in said), said)

    def test_a_relative_path_into_nothing_names_nothing(self):
        """`..` exists everywhere; the folder after it is what has to."""
        self.user_skill("deck", "---\nname: deck\n---\nSee `../nowhere/at/all.md`.\n")
        self.assertEqual([], self.said())

    def test_a_copy_with_its_own_licence_is_not_checked(self):
        self.user_skill("deck", MISSES_ITS_OWN, licence=True)
        self.assertEqual([], self.said())


class TestCardsTheCheckerNoLongerHasAreTakenBack(OwnFolderCase):
    def file_one_falsely(self, name: str) -> str:
        task = plan.add(self.ctx(), f"Skill `{name}` cannot be followed as written",
                        body="- a finding of the old checker", paths=["SKILL.md"],
                        done_when="`claude-bp skills` names nothing in this skill",
                        source=skillcheck.SOURCE)
        with store.guarded_json(store.tier_b(self.ctx(), skillcheck.FILED), default={}) as box:
            box[0] = {**(box[0] if isinstance(box[0], dict) else {}), name: "[]"}
        return task.id

    def test_the_repair_takes_back_a_false_card_and_keeps_a_true_one(self):
        self.user_skill("deck", USES_ITS_OWN, "scripts/thumbnail.py", "scripts/inventory.py",
                        "ooxml/scripts/pack.py")
        self.user_skill("broken", MISSES_ITS_OWN.replace("name: deck", "name: broken"))
        false_card = self.file_one_falsely("deck")
        true_card = self.file_one_falsely("broken")
        home = self.tmp / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            said = migrate._withdraw_skill_cards_filed_in_error(self.ctx())
        self.assertIn("1 card(s)", said)
        self.assertIsNotNone(plan.withdrawn(self.ctx(), false_card))
        self.assertEqual("next", plan.find(self.ctx(), true_card).state)
        record = store.read_json(store.tier_b(self.ctx(), skillcheck.FILED), default={})
        self.assertNotIn("deck", record)
        self.assertIn("broken", record)

    def test_a_card_a_session_claimed_is_left_with_it(self):
        task_id = self.file_one_falsely("deck")
        plan.claim(self.ctx(), task_id, "S-1", "main")
        self.assertEqual([], skillcheck.withdraw_answered(self.ctx(), []))
        self.assertEqual("doing", plan.find(self.ctx(), task_id).state)

    def test_a_card_of_another_source_is_never_taken(self):
        task = plan.add(self.ctx(), "Skill `deck` cannot be followed as written",
                        paths=["x"], done_when="stated")
        self.assertEqual([], skillcheck.withdraw_answered(self.ctx(), []))
        self.assertEqual("next", plan.find(self.ctx(), task.id).state)


class TestAReadIsCountedForASkillThatLoads(OwnFolderCase):
    def test_a_read_of_a_copy_nothing_lists_counts_nothing(self):
        """`packs/growth/skills/aso/SKILL.md` read here: only `growth:aso` loads."""
        self.write("packs/growth/skills/aso/SKILL.md", "---\nname: aso\n---\nList it.\n")
        skilluse.read_directly(self.ctx(), str(self.repo / "packs/growth/skills/aso/SKILL.md"))
        self.assertEqual({}, skilluse.counts(self.ctx()))
        self.assertEqual([], skilluse.vanished(self.ctx(), skills.inventory(self.repo)))

    def test_another_trees_copy_of_a_listed_skill_counts_under_its_name(self):
        self.skill("deploy", "---\nname: deploy\n---\nShip it.\n")
        elsewhere = self.tmp / "other-tree" / ".claude" / "skills" / "deploy" / "SKILL.md"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("---\nname: deploy\n---\nShip it.\n")
        skilluse.read_directly(self.ctx(), str(elsewhere))
        self.assertEqual({"deploy": (0, 1)}, skilluse.counts(self.ctx()))


class TestAnyDatedSkillIsNamedWhenDue(OwnFolderCase):
    def test_a_listed_skill_past_its_recheck_is_named(self):
        self.user_skill("trends", "---\nname: trends\nmetadata:\n  reviewed: \"2026-01-01\"\n"
                                  "  recheck-after-days: \"30\"\n---\nWhat is current.\n")
        listed = skills.inventory(self.repo, self.tmp / "home")
        said = skills.overdue_line(datetime.date(2026, 6, 1), listed=listed)
        self.assertIn("trends (reviewed 2026-01-01, due 2026-01-31)", said)

    def test_one_that_dates_nothing_is_not(self):
        self.user_skill("plain", "---\nname: plain\n---\nTimeless.\n")
        listed = skills.inventory(self.repo, self.tmp / "home")
        self.assertNotIn("plain", skills.overdue_line(datetime.date(2030, 1, 1), listed=listed))


class TestEverySkillIsListedWithWhereFromAndHowRead(OwnFolderCase):
    def test_each_line_names_the_skill_its_source_and_its_counts(self):
        self.skill("deploy", "---\nname: deploy\n---\nShip it.\n")
        skilluse.used(self.ctx(), "deploy")
        lines = skilluse.inventory_lines(self.ctx(), self.tmp / "home")
        line = next(line for line in lines if line.startswith("deploy"))
        self.assertIn("project", line)
        self.assertIn("used 1, read by file 0", line)
