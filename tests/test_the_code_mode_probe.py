"""The Code Mode probe reads its log the way decision 0103 needs it read.

`tools/capture_payloads.py --code-mode` runs once, in the founder's terminal, and nobody checks
its arithmetic there: a verdict that counted a shell call made outside a script, or the script's
own PostToolUse as its call's, would close decision 0103's open question on a fact nobody
measured. And what its probe writes is field names and tool names, never what a call carried.

Its first run in a founder's terminal was on Claude Code 2.1.314, which has no Code Mode: a
session opened, no script ran, and the verdict could only say so after the fact. A version too
old to start Code Mode beside the probe's mod is now refused by name, before any session.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO_ROOT

_SPEC = importlib.util.spec_from_file_location("capture_payloads",
                                               REPO_ROOT / "tools" / "capture_payloads.py")
capture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(capture)


def shape(event: str, tool: str, ident: str = "", **extra) -> dict:
    return {"event": event, "tool": tool, "id": ident, "keys": [], **extra}


A_SCRIPT = [shape("PreToolUse", "REPL", "s1"), shape("PreToolUse", "Bash", "b1")]
ITS_END = [shape("PostToolUse", "REPL", "s1", tools_used=["Bash"])]


class TestTheVerdictCountsOnlyAScriptsCalls(unittest.TestCase):
    def test_a_calls_own_post_tool_use_inside_a_script_is_seen(self):
        verdict = capture.code_mode_verdict(A_SCRIPT + [shape("PostToolUse", "Bash", "b1")] + ITS_END)
        self.assertTrue(verdict["code_mode"])
        self.assertTrue(verdict["post_tool_use"])
        self.assertTrue(verdict["post_tool_use_same_id"])
        self.assertEqual(["Bash"], verdict["tools_used"])

    def test_the_scripts_own_post_tool_use_is_not_its_calls(self):
        verdict = capture.code_mode_verdict(A_SCRIPT + ITS_END)
        self.assertTrue(verdict["pre_tool_use"])
        self.assertFalse(verdict["post_tool_use"])
        self.assertFalse(verdict["post_tool_use_same_id"])

    def test_a_shell_call_outside_a_script_answers_nothing(self):
        outside = [shape("PreToolUse", "Bash", "b0"), shape("PostToolUse", "Bash", "b0")]
        verdict = capture.code_mode_verdict(outside + A_SCRIPT + ITS_END + outside)
        self.assertFalse(verdict["post_tool_use"])

    def test_another_calls_post_tool_use_is_not_this_ones(self):
        verdict = capture.code_mode_verdict(A_SCRIPT + [shape("PostToolUse", "Bash", "b2")] + ITS_END)
        self.assertTrue(verdict["post_tool_use"])
        self.assertFalse(verdict["post_tool_use_same_id"])

    def test_the_mods_hook_counts_inside_a_script_only(self):
        mod = {"event": capture.MODULE_EVENT, "tool": "Bash", "id": None, "keys": []}
        self.assertTrue(capture.code_mode_verdict(A_SCRIPT + [mod] + ITS_END)["module_tool_call"])
        self.assertFalse(capture.code_mode_verdict([mod] + A_SCRIPT + ITS_END)["module_tool_call"])

    def test_no_script_is_no_code_mode(self):
        self.assertFalse(capture.code_mode_verdict([shape("PreToolUse", "Bash", "b0")])["code_mode"])


class TestTheProbeWritesNamesOnly(unittest.TestCase):
    def test_a_scripts_result_leaves_only_the_names_of_the_tools_it_used(self):
        event = {"hook_event_name": "PostToolUse", "tool_name": "REPL", "tool_use_id": "toolu_1",
                 "tool_input": {"code": "await $`echo secret-value`"},
                 "tool_response": {"result": "secret-value", "stdout": "secret-value",
                                   "toolsUsed": ["Bash", {"name": "Read", "input": "secret-value"}]}}
        with tempfile.TemporaryDirectory() as raw:
            probe, log = Path(raw) / "probe.py", Path(raw) / "log.jsonl"
            probe.write_text(capture.PROBE, encoding="utf-8")
            subprocess.run([sys.executable, "-I", str(probe), str(log)], input=json.dumps(event),
                           text=True, check=True, timeout=60)
            written = log.read_text(encoding="utf-8")
        self.assertNotIn("secret-value", written)
        recorded = json.loads(written)
        self.assertEqual(["Bash"], recorded["tools_used"])
        self.assertEqual("toolu_1", recorded["id"])
        self.assertEqual(["code"], recorded["tool_input"])

    def test_only_the_code_mode_probe_loads_a_mod(self):
        with tempfile.TemporaryDirectory() as raw:
            plain, code = Path(raw) / "plain", Path(raw) / "code"
            plain.mkdir()
            code.mkdir()
            log = code / "log.jsonl"
            capture._probe_plugin(plain)
            capture._probe_plugin(code, capture.CODE_MODE_EVENTS, module_log=log)
            plain_hooks = json.loads((plain / "probe" / "hooks" / "hooks.json").read_text())
            code_hooks = json.loads((code / "probe" / "hooks" / "hooks.json").read_text())
            module = (code / "probe" / "hooks" / "probe.ts").read_text()
        self.assertNotIn("modules", plain_hooks)
        self.assertEqual(["./probe.ts"], code_hooks["modules"])
        self.assertEqual(set(capture.CODE_MODE_EVENTS), set(code_hooks["hooks"]))
        self.assertIn(json.dumps(str(log)), module)


class TestAClaudeCodeTooOldIsRefusedBeforeASession(unittest.TestCase):
    def test_the_founders_first_run_is_refused_by_its_version(self):
        refusal = capture.code_mode_refusal("2.1.314")
        self.assertIn("2.1.314", refusal)
        self.assertIn("claude update", refusal)

    def test_code_mode_that_will_not_start_beside_a_mod_is_too_old_as_well(self):
        self.assertTrue(capture.code_mode_refusal("2.1.326"))

    def test_a_version_is_compared_by_number_not_by_text(self):
        # As text "2.1.40" sorts after "2.1.327", and a probe on it would open a session for nothing.
        self.assertTrue(capture.code_mode_refusal("2.1.40"))

    def test_the_first_version_that_starts_it_runs_and_so_do_later_ones(self):
        for version in ("2.1.327", "2.1.329", "2.10.0", "3.0.0"):
            with self.subTest(version=version):
                self.assertEqual("", capture.code_mode_refusal(version))

    def test_no_version_at_all_is_a_refusal(self):
        self.assertIn("PATH", capture.code_mode_refusal(""))

    def test_the_prompt_comes_before_the_flag_that_takes_every_word_after_it(self):
        command = capture.code_mode_command(Path("/probe"))
        self.assertEqual(capture.CODE_MODE_PROMPT, command[1])
        self.assertLess(command.index(capture.CODE_MODE_PROMPT), command.index("--allowedTools"))

    def test_a_refused_run_opens_no_session_and_writes_no_fixture(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.object(capture, "_version", return_value="2.1.314"), \
                mock.patch.object(capture.subprocess, "run", side_effect=AssertionError("ran")), \
                mock.patch.object(capture, "CODE_MODE_FIXTURE", Path(raw) / "fixture.json"), \
                contextlib.redirect_stdout(out):
            status = capture._code_mode(Path(raw), Path(raw))
            wrote = (Path(raw) / "fixture.json").exists()
        self.assertEqual(1, status)
        self.assertFalse(wrote)
        self.assertIn("claude update", out.getvalue())


if __name__ == "__main__":
    unittest.main()
