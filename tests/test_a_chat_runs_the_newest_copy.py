"""A chat started before an update is gated by the copy installed since, and the plugin brings
its own updates to disk (decision 0102).

Measured on Claude Code 2.1.325: a chat keeps running the hook commands of the copy it loaded,
and `/reload-plugins` asked by a plugin answers "/reload-plugins isn't available over a remote
connection in this session"; `$.prompt.submit` of the same text is refused as running a command
as the user. The founder: «я не хочу сам ничего выполнять все должно было обновиться в этом
чате и всем соседним активным чатам».
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import BIN, REPO_ROOT

from claude_bestpractice import selfupdate, upgrade

from test_gates import GateCase

PROBE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from claude_bestpractice import upgrade as _upgrade
_upgrade.handover(Path(__file__))
print(json.dumps({"ran": Path(__file__).resolve().parent.parent.name, "stdin": sys.stdin.read(),
                  "argv": sys.argv[1:], "root": os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
                  "loaded": os.environ.get(_upgrade.LOADED_FROM, "")}))
sys.exit(7)
'''


class TestAnOldCopyHandsOver(unittest.TestCase):
    """`cache/<marketplace>/<plugin>/<version>/`, as the CLI writes it, with this library."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.lib = cls.tmp / "lib"
        shutil.copytree(REPO_ROOT / "plugin" / "lib", cls.lib,
                        ignore=shutil.ignore_patterns("__pycache__"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.plugin = Path(tempfile.mkdtemp(dir=self.tmp)) / "market" / "bestpractice"

    def copy(self, version: str, probe: bool = True) -> Path:
        root = self.plugin / version
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
        shutil.copytree(self.lib, root / "lib")
        (root / "bin").mkdir()
        if probe:
            (root / "bin" / "probe").write_text(PROBE)
        return root

    def ran(self, root: Path, **env: str) -> tuple[dict, int]:
        proc = subprocess.run([sys.executable, str(root / "bin" / "probe"), "--flag", "x"],
                              input="the event", capture_output=True, text=True, timeout=60,
                              env={**{k: v for k, v in os.environ.items()
                                      if k not in (upgrade.LOADED_FROM, upgrade.NO_HANDOVER)}, **env})
        return json.loads(proc.stdout), proc.returncode

    def test_the_old_copys_command_is_the_new_copys_with_the_same_input_and_status(self):
        old, new = self.copy("2.18.0"), self.copy("2.19.0")
        said, code = self.ran(old)
        self.assertEqual(("2.19.0", "the event", ["--flag", "x"]), (said["ran"], said["stdin"], said["argv"]))
        self.assertEqual(7, code)
        self.assertEqual(str(new), said["root"])
        self.assertEqual(str(old), said["loaded"])

    def test_the_newest_copy_runs_itself(self):
        self.copy("2.18.0")
        self.assertEqual("2.19.0", self.ran(self.copy("2.19.0"))[0]["ran"])

    def test_a_newer_copy_the_cli_left_behind_is_not_handed_to(self):
        """A downgrade marks the newer version `.orphaned_at`: the founder chose the old one."""
        old = self.copy("2.18.0")
        (self.copy("2.19.0") / upgrade.ORPHAN_MARKER).write_text("1")
        self.assertEqual("2.18.0", self.ran(old)[0]["ran"])

    def test_a_newer_copy_without_the_command_leaves_it_to_this_one(self):
        old = self.copy("2.18.0")
        self.copy("2.19.0", probe=False)
        self.assertEqual("2.18.0", self.ran(old)[0]["ran"])

    def test_the_founders_switch_keeps_each_copy_on_its_own_code(self):
        old = self.copy("2.18.0")
        self.copy("2.19.0")
        self.assertEqual("2.18.0", self.ran(old, **{upgrade.NO_HANDOVER: "1"})[0]["ran"])

    def test_a_newer_name_for_this_same_copy_runs_it_once(self):
        """The newer copy's process is never handed over again: a version directory that is
        this one under another name would otherwise exec itself for ever."""
        old = self.copy("2.18.0")
        (self.plugin / "2.19.0").symlink_to(old, target_is_directory=True)
        said, code = self.ran(old)
        self.assertEqual(("2.18.0", 7), (said["ran"], code))

    def test_where_there_is_no_execve_the_newer_copy_runs_as_a_child_and_its_status_is_kept(self):
        old, new = self.copy("2.18.0"), self.copy("2.19.0")
        done = subprocess.CompletedProcess([], 5)
        with mock.patch.object(upgrade, "EXECVE", False), \
                mock.patch.object(upgrade.subprocess, "run", return_value=done) as run, \
                mock.patch.dict(os.environ, {upgrade.LOADED_FROM: "", upgrade.NO_HANDOVER: ""}), \
                self.assertRaises(SystemExit) as ended:
            upgrade.handover(old / "bin" / "probe")
        self.assertEqual(5, ended.exception.code)
        argv, env = run.call_args.args[0], run.call_args.kwargs["env"]
        self.assertEqual(str(new / "bin" / "probe"), argv[1])
        self.assertEqual((str(old), str(new)), (env[upgrade.LOADED_FROM], env["CLAUDE_PLUGIN_ROOT"]))

    def test_ten_outranks_nine(self):
        old = self.copy("2.9.0")
        self.copy("2.10.0")
        self.assertEqual("2.10.0", self.ran(old)[0]["ran"])


class TestEveryCommandHandsOverFirst(unittest.TestCase):
    def test_each_script_calls_it_before_it_reads_anything(self):
        for path in sorted(BIN.iterdir()):
            if path.suffix == ".cmd" or not path.is_file() or not path.read_text().startswith("#!"):
                continue
            text = path.read_text()
            self.assertIn("_upgrade.handover(Path(__file__))", text, path.name)
            first = min((text.find(word) for word in ("read_event", "sys.stdin", "runpy.run_path")
                         if word in text), default=len(text))
            self.assertLess(text.find("_upgrade.handover"), first, path.name)


class TestTheChatIsToldWhatStillWaits(unittest.TestCase):
    def test_a_handed_over_process_names_the_version_the_chat_loaded(self):
        with mock.patch.dict(os.environ, {upgrade.LOADED_FROM: "/c/market/bestpractice/2.18.0"}):
            said = upgrade.stale_line()
        self.assertIn("started on claude-bestpractice 2.18.0", said)
        self.assertIn("`/reload-plugins` typed by the founder", said)


class SelfUpdateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        cleared = {name: "" for name in (selfupdate.NO_SELF_UPDATE, "DISABLE_AUTOUPDATER", "DISABLE_UPDATES",
                                         "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                                         "FORCE_AUTOUPDATE_PLUGINS", "CLAUDE_PLUGIN_DATA")}
        patched = mock.patch.dict(os.environ, cleared)
        patched.start()
        self.addCleanup(patched.stop)
        self.project = self.home / "project"
        self.project.mkdir()

    def install(self, **plugins: list[dict]) -> None:
        path = self.home / selfupdate.INSTALLED
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 2, "plugins": {
            f"{name}@claude-bestpractice": entries for name, entries in plugins.items()}}))

    def calls(self, state: dict | None = None) -> list[tuple[str, Path | None]]:
        return [(" ".join(argv), cwd) for argv, cwd in selfupdate.plan([self.project], state or {}, self.home)]


class TestWhatAnUpdateRuns(SelfUpdateCase):
    def test_the_catalog_first_then_every_install_this_session_can_name_then_curated(self):
        other = self.home / "elsewhere"
        self.install(bestpractice=[{"scope": "user"}, {"scope": "project", "projectPath": str(self.project)},
                                   {"scope": "project", "projectPath": str(other)}],
                     design=[{"scope": "user"}])
        self.assertEqual([
            ("plugin marketplace update claude-bestpractice", None),
            ("plugin update bestpractice@claude-bestpractice --scope user", None),
            ("plugin update bestpractice@claude-bestpractice --scope project", self.project),
            ("plugin update design@claude-bestpractice --scope user", None),
            ("plugin install curated@claude-bestpractice --scope user", None),
        ], self.calls())

    def test_curated_is_installed_once_and_never_again_once_removed(self):
        self.install(bestpractice=[{"scope": "user"}])
        self.assertNotIn(("plugin install curated@claude-bestpractice --scope user", None),
                         self.calls({"curated_installed_at": 1.0}))
        self.install(bestpractice=[{"scope": "user"}], curated=[{"scope": "user"}])
        self.assertIn(("plugin update curated@claude-bestpractice --scope user", None), self.calls())
        self.assertNotIn("install", " ".join(call for call, _ in self.calls()))


class TestWhenNoUpdateRuns(SelfUpdateCase):
    def test_claude_codes_switches_and_ours(self):
        for name in ("DISABLE_AUTOUPDATER", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
            with mock.patch.dict(os.environ, {name: "1"}):
                self.assertIn(name, selfupdate.off(self.home))
                with mock.patch.dict(os.environ, {"FORCE_AUTOUPDATE_PLUGINS": "1"}):
                    self.assertEqual("", selfupdate.off(self.home))
        with mock.patch.dict(os.environ, {selfupdate.NO_SELF_UPDATE: "1"}):
            self.assertIn(selfupdate.NO_SELF_UPDATE, selfupdate.off(self.home))
        self.assertEqual("", selfupdate.off(self.home))

    def test_the_founder_turning_this_marketplaces_auto_update_off(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"extraKnownMarketplaces": {"claude-bestpractice": {"autoUpdate": False}}}))
        self.assertIn("autoUpdate is false", selfupdate.off(self.home))

    def test_at_most_once_in_six_hours_on_the_machine(self):
        now = time.time()
        self.assertTrue(selfupdate.due(self.home, now))
        self.assertFalse(selfupdate.due(self.home, now + 60))
        self.assertTrue(selfupdate.due(self.home, now + selfupdate.INTERVAL + 1))

    def test_a_development_checkout_starts_nothing(self):
        with mock.patch.object(selfupdate.subprocess, "Popen") as popen:
            selfupdate.start([self.project], self.home)
        popen.assert_not_called()


class TestARunIsRecordedAndToldOnce(SelfUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        fake = self.home / "bin" / "claude"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\ncase \"$2\" in\n  update) echo \"Plugin updated from 2.18.0 to 2.19.0\";;\n"
                        "  install) echo \"Successfully installed plugin: $3\";;\n"
                        "  *) echo \"Successfully updated marketplace\";;\nesac\n")
        fake.chmod(0o755)
        patched = mock.patch.dict(os.environ, {"PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"})
        patched.start()
        self.addCleanup(patched.stop)
        self.install(bestpractice=[{"scope": "user"}])

    def test_each_call_and_its_answer_is_kept_and_curated_marked_installed(self):
        ran = selfupdate.run([self.project], self.home)
        self.assertEqual([0, 0, 0], [r["exit"] for r in ran])
        state = json.loads((selfupdate.data_dir(self.home) / selfupdate.STATE).read_text())
        self.assertIn("curated_installed_at", state)
        self.assertIn("plugin install curated@claude-bestpractice", selfupdate.summary(self.home))

    def test_the_board_says_what_it_brought_once(self):
        selfupdate.run([self.project], self.home)
        said = selfupdate.line(self.home)
        self.assertIn("bestpractice@claude-bestpractice", said)
        self.assertIn("curated@claude-bestpractice", said)
        self.assertEqual("", selfupdate.line(self.home))


class TestTheNextSessionStartSaysWhatItBrought(GateCase):
    def test_once(self):
        data = self.tmp / "plugin-data"
        data.mkdir()
        (data / selfupdate.STATE).write_text(json.dumps({"finished_at": 1.0, "told": False, "ran": [
            {"call": "plugin update bestpractice@claude-bestpractice --scope user", "exit": 0,
             "said": 'Plugin "bestpractice" updated from 2.18.0 to 2.19.0 for scope user.'}]}))
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data)}):
            first, second = self.start().stdout, self.start().stdout
        self.assertIn("updated itself: bestpractice@claude-bestpractice", first)
        self.assertNotIn("updated itself", second)


if __name__ == "__main__":
    unittest.main()
