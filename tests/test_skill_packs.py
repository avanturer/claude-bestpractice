"""Curated skills ship as packs: plugins of their own in this marketplace (decision 0101).

A pack is updated by Claude Code like any plugin (a new version's copy replaces the old, so a
skill added appears and a skill removed goes), held to the plugin's own checks, and a skill
copied from another repository says where from, at which commit and under which licence, and is
compared with its upstream every week. `curated` depends on every pack and is installed once at
user scope, so each comes to every project (decision 0101).
"""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import subprocess
import sys
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
        """A pack is advice, chosen by the model; enforcement is the plugin's (decision 0101)."""
        for manifest in self.packs:
            pack = manifest.parent.parent
            for forbidden in ("hooks", ".mcp.json", ".lsp.json", "bin", "monitors"):
                self.assertFalse((pack / forbidden).exists(), f"{pack.name} has {forbidden}")

    def test_the_landing_skill_lives_in_the_design_pack_not_the_plugin(self):
        self.assertTrue((PACKS / "design" / "skills" / "landing-not-slop" / "SKILL.md").is_file())
        self.assertNotIn("landing-not-slop", [skill.name for skill in skills.ours()])
        self.assertEqual("design@claude-bestpractice", skills.MOVED["landing-not-slop"])


class TestCuratedBringsEveryPackEverywhere(unittest.TestCase):
    """Measured on 2.1.325 in a throwaway HOME: `claude plugin install curated@…` installs each
    dependency at user scope and enables it in the user's settings; `claude plugin update` of a
    version that lists one more installs it; one delisted from the marketplace is uninstalled at
    the next session start with `forceRemoveDeletedPlugins`, and stays, failing to load, without
    it; `renames: {<name>: null}` removes nothing."""

    def setUp(self) -> None:
        self.marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.curated = json.loads((PACKS / "curated" / ".claude-plugin" / "plugin.json").read_text())

    def test_it_depends_on_every_other_pack_of_the_marketplace(self):
        packs = sorted(entry["name"] for entry in self.marketplace["plugins"]
                       if entry["source"].startswith("./packs/") and entry["name"] != "curated")
        self.assertEqual(packs, sorted(self.curated["dependencies"]))

    def test_each_by_bare_name_with_no_range(self):
        """A bare name resolves in this marketplace; a range a relative-path pack's copy falls
        outside of keeps `curated` disabled."""
        for dependency in self.curated["dependencies"]:
            self.assertIsInstance(dependency, str)
            self.assertNotIn("@", dependency)

    def test_it_ships_nothing_of_its_own(self):
        self.assertEqual([".claude-plugin"], sorted(path.name for path in (PACKS / "curated").iterdir()))

    def test_the_plugin_depends_on_no_pack(self):
        """A dependency that cannot load keeps its dependent disabled: one pack turned off in a
        project would turn off every gate there."""
        manifest = json.loads((REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        entry = next(e for e in self.marketplace["plugins"] if e["name"] == "bestpractice")
        self.assertNotIn("dependencies", manifest)
        self.assertNotIn("dependencies", entry)

    def test_a_pack_delisted_leaves_every_install(self):
        self.assertIs(True, self.marketplace.get("forceRemoveDeletedPlugins"))
        self.assertNotIn(None, self.marketplace.get("renames", {}).values())


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


class TestACopyIsMadeByTheVendorTool(unittest.TestCase):
    """`tools/vendor_skill.py NAME` makes the copy `tools/vendored.json` describes, one `make
    skills` accepts as it stands."""

    BODY = ("See [the checklist](../../references/checklist.md) and "
            "![the flow](images/flow.png).\n\n## Steps\n\nDo it.\n\n"
            "## Tools\n\n```md\n## Not a heading inside a fence\n```\n\nUse a profiler.\n\n"
            "## Related Skills\n\nnone\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.checkout = self.tmp / "upstream"
        source = self.checkout / "skills" / "harden"
        (source / "agents").mkdir(parents=True)
        (source / "agents" / "openai.yaml").write_text("x\n")
        (source / "references" / "images").mkdir(parents=True)
        (source / "references" / "images" / "flamegraph.png").write_bytes(b"\x89PNG")
        (source / "references" / "profile.md").write_text(
            "Record a trace.\n\n![A flame graph of one frame](images/flamegraph.png)\n")
        (source / "scripts").mkdir()
        (source / "scripts" / "count.mjs").write_text("const words = /[a-z]+/g;\n")
        (self.checkout / "references").mkdir()
        (self.checkout / "references" / "checklist.md").write_text("- [ ] one\n")
        (self.checkout / "LICENSE").write_text("MIT License\n")
        (source / "SKILL.md").write_text(
            "---\nname: harden\ndescription: an upstream description\nlicense: MIT\n"
            "allowed-tools: Read, Grep\nmetadata:\n  author: someone\n---\n\n" + self.BODY)
        run = lambda *args: subprocess.run(["git", *args], cwd=self.checkout, check=True,
                                           capture_output=True)
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        self.root = self.tmp / "repo"
        (self.root / "packs").mkdir(parents=True)
        (self.root / "tools").mkdir()
        self.copy = self.root / "packs" / "engineering" / "skills" / "harden"
        self.vendor = tool("vendor_skill")
        self.entry = {"repo": "acme/skills", "path": "skills/harden", "pack": "engineering",
                      "license": "MIT", "recheck_days": 60, "description": "Harden code."}

    def vendored(self, **extra) -> int:
        manifest = self.root / "tools" / "vendored.json"
        manifest.write_text(json.dumps({"harden": {**self.entry, **extra}}))
        with mock.patch.object(self.vendor, "ROOT", self.root), \
                mock.patch.object(self.vendor, "MANIFEST", manifest), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.vendor.main(["harden", "--checkout", str(self.checkout)])

    def text(self, name: str = "SKILL.md") -> str:
        return (self.copy / name).read_text()

    def test_the_copy_passes_the_packs_checks(self):
        self.assertEqual(0, self.vendored(guard="Not here."))
        self.assertEqual([], skills.malformed(self.copy / "SKILL.md", datetime.date.today()))
        self.assertIn("allowed-tools: Read, Grep", self.text())
        self.assertIn('recheck-after-days: "60"', self.text())
        self.assertNotIn("author: someone", self.text())
        self.assertIn("## Before following this here\n\nNot here.", self.text())
        self.assertTrue((self.copy / "LICENSE").is_file())
        self.assertFalse((self.copy / "agents").exists())

    def test_a_file_linked_outside_the_skills_folder_comes_with_it(self):
        self.vendored()
        self.assertIn("](references/checklist.md)", self.text())
        self.assertEqual("- [ ] one\n", self.text("references/checklist.md"))

    def test_an_image_is_left_out_and_its_caption_kept_in_every_file(self):
        self.vendored()
        self.assertFalse((self.copy / "references" / "images").exists())
        self.assertIn("*Figure left out of this copy: the flow*", self.text())
        self.assertEqual("Record a trace.\n\n*Figure left out of this copy: A flame graph of one "
                         "frame*\n", self.text("references/profile.md"))

    def test_a_section_moved_out_leaves_its_heading_and_the_file_it_went_to(self):
        self.vendored(move_sections=["Tools"])
        self.assertIn("## Tools\n\nRead `references/tools.md` for this part.\n\n## Related Skills",
                      self.text())
        self.assertEqual("# Tools\n\n```md\n## Not a heading inside a fence\n```\n\nUse a "
                         "profiler.\n", self.text("references/tools.md"))
        self.assertIn("## Steps\n\nDo it.", self.text())

    def test_a_section_named_by_no_heading_or_by_several_is_refused(self):
        """A `## ` line inside a fence is no heading."""
        for wanted, count in (("Missing", 0), ("Not a heading", 0), ("", 3)):
            with self.assertRaises(SystemExit) as raised:
                self.vendored(move_sections=[wanted])
            self.assertIn(f"{count} sections start", str(raised.exception.code))

    def test_its_patch_is_applied_and_one_that_no_longer_fits_stops_the_run(self):
        patch = self.root / "tools" / "harden.patch"
        patch.write_text("--- a/scripts/count.mjs\n+++ b/scripts/count.mjs\n@@ -1 +1 @@\n"
                         "-const words = /[a-z]+/g;\n+const words = /\\p{L}+/gu;\n")
        self.vendored(patch="tools/harden.patch")
        self.assertEqual("const words = /\\p{L}+/gu;\n", self.text("scripts/count.mjs"))
        (self.checkout / "skills" / "harden" / "scripts" / "count.mjs").write_text("moved on\n")
        with self.assertRaises(SystemExit) as raised:
            self.vendored(patch="tools/harden.patch")
        self.assertIn("no longer applies", str(raised.exception.code))

    def test_a_licence_that_does_not_allow_a_changed_copy_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            self.vendored(license="CC-BY-NC-4.0")
        self.assertIn("does not allow a changed copy", str(raised.exception.code))
        self.assertFalse(self.copy.exists())


class TestEveryCopyIsTheOneItsEntryDescribes(unittest.TestCase):
    """A copy whose entry says something else would be undone by the next refresh."""

    def setUp(self) -> None:
        self.entries = json.loads((REPO_ROOT / "tools" / "vendored.json").read_text())
        self.copies = {path.parent.name: path for path in PACKS.glob("*/skills/*/SKILL.md")
                       if skills.upstream(path)}

    def test_each_copy_has_an_entry_and_each_entry_a_copy(self):
        self.assertEqual(sorted(self.copies), sorted(self.entries))

    def test_each_entry_names_the_copys_pack_upstream_and_description(self):
        for name, path in self.copies.items():
            entry = self.entries[name]
            owner, repo, folder, _ = skills.upstream(path)
            self.assertEqual(entry["pack"], path.parts[-4], name)
            self.assertEqual(entry["repo"], f"{owner}/{repo}", name)
            self.assertEqual(entry["path"], folder, name)
            self.assertEqual(entry["description"], skills.meta(path.read_text())["description"])

    def test_each_patch_named_is_applied_in_the_shipped_copy(self):
        for name, entry in self.entries.items():
            if entry.get("patch"):
                added = [line[1:] for line in (REPO_ROOT / entry["patch"]).read_text().splitlines()
                         if line.startswith("+") and not line.startswith("+++")]
                shipped = "".join(path.read_text() for path in self.copies[name].parent.rglob("*")
                                  if path.is_file() and path.suffix in (".mjs", ".md", ".py"))
                for line in added:
                    self.assertIn(line, shipped, name)


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

    def test_a_changed_copy_is_named_with_the_command_that_rebuilds_it(self):
        printed = io.StringIO()
        def get(url: str) -> object:
            if "/compare/" in url:
                return {"status": "ahead"}
            return [{"sha": "c" * 40}] if "/commits?" in url else {"default_branch": "main"}
        with contextlib.redirect_stdout(printed):
            code = self.upstream.main(get)
        self.assertEqual(1, code)
        self.assertIn("Refresh: `python3 tools/vendor_skill.py video --checkout <a clone of "
                      "coreyhaines31/marketingskills>`", printed.getvalue())

    def test_with_no_copy_in_any_pack_nothing_is_asked(self):
        asked = []
        with mock.patch.object(self.upstream, "ROOT", Path(tempfile.mkdtemp())), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.upstream.main(lambda url: asked.append(url) or {}))
        self.assertEqual([], asked)

    def test_every_copy_shipped_is_one_the_weekly_job_asks_about(self):
        asked = []
        with contextlib.redirect_stdout(io.StringIO()):
            self.upstream.main(lambda url: asked.append(url) or {})
        copies = [path for path in PACKS.glob("*/skills/*/SKILL.md") if skills.upstream(path)]
        self.assertTrue(copies)
        self.assertEqual(len(copies), len(asked))
