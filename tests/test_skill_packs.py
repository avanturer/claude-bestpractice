"""Curated skills ship as packs: plugins of their own in this marketplace (decision 0100).

A pack is enabled per project, updated by Claude Code like any plugin (a new version's copy
replaces the old, so a skill added appears and a skill removed goes), held to the plugin's own
checks, and a skill copied from another repository says where from, at which commit and under
which licence, and is compared with its upstream every week.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_bestpractice import skills

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS = REPO_ROOT / "packs"


def tool(name: str):
    """A module of `tools/`, loaded from its file."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEachPackIsAPluginOfTheMarketplace(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = {entry["name"]: entry for entry in json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())["plugins"]}
        self.packs = sorted(PACKS.glob("*/.claude-plugin/plugin.json"))

    def test_there_is_one(self):
        self.assertTrue(self.packs)

    def test_each_is_listed_under_its_folder_with_the_same_version(self):
        for manifest in self.packs:
            pack = manifest.parent.parent
            declared = json.loads(manifest.read_text())
            self.assertEqual(pack.name, declared["name"], manifest)
            entry = self.entries.get(pack.name)
            self.assertIsNotNone(entry, f"{pack.name} is not in the marketplace")
            self.assertEqual(f"./packs/{pack.name}", entry["source"])
            self.assertEqual(declared["version"], entry["version"], pack.name)

    def test_no_pack_carries_hooks_or_servers(self):
        """A pack is advice, chosen by the model; enforcement is the plugin's (decision 0100)."""
        for manifest in self.packs:
            pack = manifest.parent.parent
            for forbidden in ("hooks", ".mcp.json", ".lsp.json", "bin", "monitors"):
                self.assertFalse((pack / forbidden).exists(), f"{pack.name} has {forbidden}")

    def test_the_landing_skill_lives_in_the_design_pack_not_the_plugin(self):
        self.assertTrue((PACKS / "design" / "skills" / "landing-not-slop" / "SKILL.md").is_file())
        self.assertNotIn("landing-not-slop", [skill.name for skill in skills.ours()])
        self.assertEqual("design@claude-bestpractice", skills.MOVED["landing-not-slop"])


class TestAPacksChangeNeedsItsOwnBump(unittest.TestCase):
    """`claude plugin update` compares the version; a pack's change under an unchanged one
    reaches nobody, exactly as the plugin's does (`tools/check_shipped.py`)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        run = lambda *args: subprocess.run(["git", *args], cwd=self.root, check=True,
                                           capture_output=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (self.root / ".claude-plugin").mkdir()
        self.pack = self.root / "packs" / "design"
        (self.pack / ".claude-plugin").mkdir(parents=True)
        (self.pack / "skills" / "x").mkdir(parents=True)
        self.write_versions("1.0.0", "1.0.0")
        (self.pack / "skills" / "x" / "SKILL.md").write_text("one\n")
        run("add", "-A")
        run("commit", "-qm", "base")
        run("tag", "base")
        self.shipped = tool("check_shipped")
        patched = mock.patch.object(self.shipped, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def write_versions(self, pack: str, listed: str) -> None:
        (self.pack / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "design", "version": pack}))
        (self.root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"plugins": [{"name": "design", "source": "./packs/design", "version": listed}]}))

    def test_an_unchanged_pack_passes(self):
        self.assertEqual([], self.shipped.pack_problems("base"))

    def test_a_change_without_a_bump_is_refused(self):
        (self.pack / "skills" / "x" / "SKILL.md").write_text("two\n")
        found = self.shipped.pack_problems("base")
        self.assertEqual(1, len(found))
        self.assertIn("still 1.0.0", found[0])

    def test_a_change_with_a_bump_passes(self):
        (self.pack / "skills" / "x" / "SKILL.md").write_text("two\n")
        self.write_versions("1.1.0", "1.1.0")
        self.assertEqual([], self.shipped.pack_problems("base"))

    def test_a_bump_in_one_place_only_is_refused(self):
        self.write_versions("1.1.0", "1.0.0")
        self.assertIn("marketplace entry 1.0.0", self.shipped.pack_problems(None)[0])


class TestACopySaysWhereItCameFrom(unittest.TestCase):
    SHA = "0123456789abcdef0123456789abcdef01234567"

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp()) / "frontend-design"
        self.folder.mkdir()

    def skill(self, metadata: str) -> Path:
        path = self.folder / "SKILL.md"
        path.write_text(f"---\nname: frontend-design\ndescription: x\nmetadata:\n"
                        f'  reviewed: "2026-11-14"\n{metadata}---\n\nbody\n')
        return path

    def test_one_of_our_own_needs_nothing(self):
        self.assertEqual([], skills._provenance(self.skill(""), {}))

    def test_a_copy_names_upstream_commit_and_a_licence_that_allows_it_with_its_text(self):
        (self.folder / "LICENSE").write_text("Apache License 2.0\n")
        path = self.skill('  upstream: "github.com/acme/skills/skills/frontend-design"\n'
                          f'  commit: "{self.SHA}"\n  license: "Apache-2.0"\n')
        self.assertEqual([], skills.malformed(path, skills.datetime.date(2026, 11, 14)))
        self.assertEqual(("acme", "skills", "skills/frontend-design", self.SHA), skills.upstream(path))

    def test_a_copy_missing_any_of_it_is_refused(self):
        path = self.skill('  upstream: "github.com/acme/skills/skills/frontend-design"\n'
                          '  commit: "main"\n  license: "proprietary"\n')
        said = " ".join(skills.malformed(path, skills.datetime.date(2026, 11, 14)))
        self.assertIn("40-character commit", said)
        self.assertIn("a copy ships only under one of", said)
        self.assertIn("LICENSE", said)


class TestAnUpstreamThatMovedIsNamed(unittest.TestCase):
    PINNED = "a" * 40

    def setUp(self) -> None:
        self.upstream = tool("check_upstream")

    def github(self, latest: list, status: str = "ahead"):
        def get(url: str) -> object:
            if "/compare/" in url:
                return {"status": status}
            if "/commits?" in url:
                self.assertIn("path=skills%2Ffrontend-design", url)
                return latest
            return {"default_branch": "main"}
        return get

    def moved(self, get) -> str:
        return self.upstream.moved("acme", "skills", "skills/frontend-design", self.PINNED, get)

    def test_a_folder_last_changed_at_the_pinned_commit_has_not_moved(self):
        self.assertEqual("", self.moved(self.github([{"sha": self.PINNED}])))

    def test_a_folder_last_changed_before_the_pinned_commit_has_not_moved(self):
        self.assertEqual("", self.moved(self.github([{"sha": "b" * 40}], status="behind")))

    def test_a_folder_changed_after_it_is_named_with_the_diff_to_read(self):
        said = self.moved(self.github([{"sha": "c" * 40}], status="ahead"))
        self.assertIn(f"compare/{self.PINNED}...{'c' * 40}", said)

    def test_a_folder_gone_upstream_is_named(self):
        self.assertIn("is gone", self.moved(self.github([])))

    def test_with_no_copy_in_any_pack_nothing_is_asked(self):
        asked = []
        self.assertEqual(0, self.upstream.main(lambda url: asked.append(url) or {}))
        self.assertEqual([], asked)
