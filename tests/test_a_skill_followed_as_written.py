"""A skill that cannot be followed as written is named `file:line` and filed as a card (#364).

The audit that opened #364 found half of one repository's skills unfollowable: a script sourced
through `$CLAUDE_PROJECT_DIR`, empty in the Bash tool; an entry point that had been renamed; a
stacked-PR command that merged in a loop, which the merge gate refuses. Nothing had said so.
"""

from __future__ import annotations

import json
import subprocess
import sys

from helpers import BIN

from claude_bestpractice import plan, skillcheck, skills, store

from test_gates import GateCase

BROKEN = """---
name: stack
description: land a stack
---

Read `docs/old-guide.md` first, then `docs/guide.md`.

```bash
source $CLAUDE_PROJECT_DIR/.claude/scripts/env.sh
python3 scripts/scrape.py --all
make deploy-preview
npm run lint:fix
for pr in 101 102; do gh pr merge $pr --squash; done
git push origin main
```
"""

SOUND = """---
name: release
description: cut a release
---

```bash
source ${CLAUDE_PROJECT_DIR}/scripts/env.sh
python3 scripts/check.py \\
  --strict
make test
npm run lint
gh pr merge 12 --squash
git push origin feature/x
```

Talking about `gh pr merge` in a loop is not doing it:

```
for pr in 1 2; do gh pr merge $pr; done
```
"""


class SkillCheckCase(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("Makefile", ".PHONY: test\ntest:\n\tpytest\n")
        self.write("package.json", json.dumps({"scripts": {"lint": "eslint ."}}))
        self.write("scripts/env.sh", "export X=1\n")
        self.write("scripts/check.py", "print('ok')\n")
        self.write("docs/guide.md", "guide\n")

    def skill(self, name: str, text: str) -> None:
        self.write(f".claude/skills/{name}/SKILL.md", text)

    def findings(self) -> list[skillcheck.Finding]:
        return skillcheck.check(self.ctx(), skills.inventory(self.repo, self.tmp / "home"))


class TestWhatCannotBeFollowedIsNamed(SkillCheckCase):
    def setUp(self) -> None:
        super().setUp()
        self.skill("stack", BROKEN)
        self.said = {finding.line: finding.said for finding in self.findings()}

    def test_a_path_named_in_prose_that_is_gone(self):
        self.assertIn("docs/old-guide.md", self.said[6])
        self.assertNotIn("docs/guide.md`,", self.said[6])

    def test_a_variable_the_bash_tool_never_has(self):
        self.assertIn("$CLAUDE_PROJECT_DIR` is empty in the Bash tool", self.said[9])

    def test_a_script_that_was_renamed(self):
        self.assertIn("scripts/scrape.py", self.said[10])

    def test_a_make_target_and_an_npm_script_that_do_not_exist(self):
        self.assertIn("no target `deploy-preview`", self.said[11])
        self.assertIn("no script `lint:fix`", self.said[12])

    def test_a_step_the_merge_gate_refuses(self):
        self.assertIn("the merge gate refuses this step", self.said[13])

    def test_a_push_to_the_trunk(self):
        self.assertIn("pushes to the trunk `main`", self.said[14])

    def test_each_is_named_by_file_and_line(self):
        where = skillcheck.lines(self.ctx(), self.findings())
        self.assertTrue(where[0].startswith(".claude/skills/stack/SKILL.md:6 stack:"), where)


class TestASoundSkillPasses(SkillCheckCase):
    def test_nothing_is_said_about_it(self):
        self.skill("release", SOUND)
        self.assertEqual([], self.findings())

    def test_a_synced_or_plugin_skill_is_not_this_repositorys_to_fix(self):
        home = self.tmp / "home"
        synced = home / ".claude" / "skills" / "synced" / "org_a"
        (synced / "stack").mkdir(parents=True)
        (synced / "stack" / "SKILL.md").write_text(BROKEN)
        (synced / "manifest.json").write_text(json.dumps(
            {"lastUpdated": 1, "skills": [{"name": "stack"}]}))
        self.assertEqual([], self.findings())


class TestFindingsBecomeCards(SkillCheckCase):
    def setUp(self) -> None:
        super().setUp()
        self.skill("stack", BROKEN)

    def test_one_card_per_skill_naming_every_line(self):
        filed = skillcheck.file_cards(self.ctx(), self.findings())
        self.assertEqual(1, len(filed))
        card = plan.find(self.ctx(), filed[0])
        self.assertIn("`stack`", card.title)
        self.assertIn(".claude/skills/stack/SKILL.md:13", card.body)
        self.assertEqual([".claude/skills/stack/SKILL.md"], card.paths)

    def test_filed_once_for_the_same_findings(self):
        skillcheck.file_cards(self.ctx(), self.findings())
        self.assertEqual([], skillcheck.file_cards(self.ctx(), self.findings()))

    def test_a_reindex_files_nothing_twice(self):
        skillcheck.file_cards(self.ctx(), self.findings())
        store.purge_tier_b(self.ctx())
        self.assertEqual([], skillcheck.file_cards(self.ctx(), self.findings()))

    def test_filed_again_when_the_findings_change(self):
        skillcheck.file_cards(self.ctx(), self.findings())
        self.skill("stack", BROKEN.replace("make deploy-preview", "make test"))
        self.assertEqual(1, len(skillcheck.file_cards(self.ctx(), self.findings())))

    def test_the_session_start_board_names_the_card(self):
        started = self.start()
        self.assertIn("cannot be followed as written", started.stdout)

    def test_the_command_lists_them_and_fails(self):
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp"), "skills"],
                              cwd=str(self.repo), capture_output=True, text=True, timeout=60)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(".claude/skills/stack/SKILL.md:13", proc.stdout)
