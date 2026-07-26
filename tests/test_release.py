"""What the repository claims about itself, checked against itself.

None of this is exotic; all of it shipped wrong at least once. The version lived in
three files that disagreed (0.1.0, 0.0.0, and 1.0.0 in a README badge), and the
advertised one-command install pointed at a `main` branch this repository does not
have, so the single line every new user runs first returned 404. Both are invisible to
every other gate here: they are claims about the packaging, and the packaging is the
part no test exercises.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from helpers import REPO_ROOT

READMES = ("README.md", "docs/README.ru.md", "docs/README.zh.md")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class TestVersionAgreement(unittest.TestCase):
    def declared(self) -> str:
        source = read("plugin/lib/founder_os/__init__.py")
        match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(match, "__init__.py has no __version__")
        return match.group(1)

    def test_manifests_match_the_package(self) -> None:
        version = self.declared()
        plugin = json.loads(read("plugin/.claude-plugin/plugin.json"))
        self.assertEqual(plugin["version"], version)

        market = json.loads(read("plugin/.claude-plugin/marketplace.json"))
        entries = [p for p in market["plugins"] if p["name"] == plugin["name"]]
        self.assertEqual(len(entries), 1, "plugin listed zero or twice in the marketplace")
        self.assertEqual(entries[0]["version"], version)

    def test_every_readme_badge_matches(self) -> None:
        version = self.declared()
        for rel in READMES:
            badge = re.search(r"badge/version-([0-9][^-\s)]*)-", read(rel))
            self.assertIsNotNone(badge, f"{rel} has no version badge")
            self.assertEqual(badge.group(1), version, f"{rel} badge is stale")


class TestInstallPath(unittest.TestCase):
    """The one line every new user runs before anything else works."""

    ONE_LINER = re.compile(r"raw\.githubusercontent\.com/[^/]+/[^/]+/([^/]+)/install\.sh")

    def test_the_advertised_ref_is_one_that_resolves(self) -> None:
        # `main` is the tempting default and is wrong here: this repository's default
        # branch is not called main. HEAD resolves to whatever the default branch is,
        # and keeps resolving after a rename.
        for rel in ("install.sh",) + READMES:
            for ref in self.ONE_LINER.findall(read(rel)):
                self.assertEqual(ref, "HEAD", f"{rel} advertises ref {ref!r}")

    def test_the_installer_is_executable(self) -> None:
        self.assertTrue((REPO_ROOT / "install.sh").stat().st_mode & 0o111, "install.sh is not +x")

    def test_the_license_the_manifests_claim_exists(self) -> None:
        plugin = json.loads(read("plugin/.claude-plugin/plugin.json"))
        text = read("LICENSE")
        self.assertIn(plugin["license"], text.replace(" License", ""))
        self.assertNotIn("[year]", text, "LICENSE still has a placeholder")
        self.assertNotIn("[fullname]", text, "LICENSE still has a placeholder")


class TestTranslationsStayInStep(unittest.TestCase):
    """A translation that silently stops matching is worse than no translation."""

    def test_each_readme_links_to_the_other_two(self) -> None:
        expected = {
            "README.md": ("docs/README.ru.md", "docs/README.zh.md"),
            "docs/README.ru.md": ("../README.md", "README.zh.md"),
            "docs/README.zh.md": ("../README.md", "README.ru.md"),
        }
        for rel, targets in expected.items():
            text = read(rel)
            for target in targets:
                self.assertIn(f"({target})", text, f"{rel} does not link to {target}")

    def test_relative_links_resolve(self) -> None:
        for rel in READMES:
            base = (REPO_ROOT / rel).parent
            for target in re.findall(r"\]\((?!https?:|#)([^)]+)\)", read(rel)):
                path = (base / target.split("#")[0]).resolve()
                self.assertTrue(path.exists(), f"{rel} links to missing {target}")

    def test_the_gate_table_covers_the_same_gates_everywhere(self) -> None:
        gates = {
            "setup", "session-start", "prompt-capture", "pre-tool", "review-commit",
            "worktree-create", "subagent-brief", "checkpoint", "evidence-gate",
        }
        for rel in READMES:
            text = read(rel)
            missing = {g for g in gates if f"`{g}`" not in text}
            self.assertFalse(missing, f"{rel} omits gate(s): {sorted(missing)}")

    def test_every_documented_gate_actually_ships(self) -> None:
        for rel in READMES:
            for gate in re.findall(r"^\| `([a-z-]+)` \| ", read(rel), re.M):
                self.assertTrue(
                    (REPO_ROOT / "plugin" / "bin" / gate).is_file(),
                    f"{rel} documents `{gate}`, which does not exist in plugin/bin/",
                )



class TestSkills(unittest.TestCase):
    """Skills are loaded on demand, so they cost nothing until they are relevant."""

    SKILLS = REPO_ROOT / "plugin" / "skills"

    def frontmatter(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), f"{path.name} has no frontmatter")
        block = text.split("---", 2)[1]
        return dict(
            (k.strip(), v.strip())
            for k, _, v in (line.partition(":") for line in block.splitlines())
            if k.strip()
        )

    def test_every_skill_declares_a_name_and_a_trigger(self):
        """A description that does not say WHEN to use it never gets invoked."""
        found = list(self.SKILLS.glob("*/SKILL.md"))
        self.assertGreaterEqual(len(found), 3)
        for path in found:
            with self.subTest(skill=path.parent.name):
                meta = self.frontmatter(path)
                self.assertEqual(meta.get("name"), path.parent.name)
                description = meta.get("description", "").lower()
                self.assertTrue(
                    any(trigger in description for trigger in ("use when", "use at", "use whenever")),
                    f"{path.parent.name}: the description never says WHEN to use it, "
                    "so it will never be invoked",
                )

    def test_none_of_them_is_always_on(self):
        """The always-on budget is 400 tokens; a skill loaded eagerly would blow it."""
        hooks = (REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8")
        for path in self.SKILLS.glob("*/SKILL.md"):
            self.assertNotIn(path.parent.name, hooks, f"{path.parent.name} is wired to an event")

    def test_they_are_specific_enough_to_act_on(self):
        """"Make it modern" is not actionable and produces the same page every time."""
        landing = (self.SKILLS / "landing-not-slop" / "SKILL.md").read_text(encoding="utf-8")
        for concrete in ("gradient", "375px", "4.5:1", "prefers-reduced-motion"):
            self.assertIn(concrete, landing)

        defaults = (self.SKILLS / "founder-defaults" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("[enforced]", defaults, "the defaults must say which are binding")


if __name__ == "__main__":
    unittest.main()
