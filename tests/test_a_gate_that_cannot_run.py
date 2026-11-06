"""A gate that cannot run refuses, rather than letting every call through (decision 0067).

`hookio.guard` turns any failure inside a gate into a refusal, but it can only catch what
happens once it holds the gate. A library that does not import, an interpreter that is not
there and a gate the harness kills at its timeout all end the process with some other code,
and Claude Code runs the tool on any code but 2: the session carries on with no gate at all
and nothing says so. Since 2.1.295 the entry itself can say what such a failure means.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import BIN

MANIFEST = BIN.parent / "hooks" / "hooks.json"


def handlers() -> list[tuple[str, dict]]:
    """Every hook entry in the manifest, with the event it is registered under."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [(event, handler)
            for event, groups in manifest["hooks"].items()
            for group in groups for handler in group["hooks"]]


class TestTheGateThatRefusesFailsClosedInTheHarness(unittest.TestCase):
    def test_pre_tool_says_a_failure_blocks(self):
        gates = [handler for event, handler in handlers()
                 if event == "PreToolUse" and handler["command"].endswith('/bin/pre-tool"')]
        self.assertEqual(1, len(gates))
        self.assertEqual("block", gates[0].get("onFailure"))

    def test_no_background_hook_claims_it(self):
        """Claude Code ignores `onFailure` on a hook it runs in the background: there is no
        action left to block. Written there, it would read as a guarantee nobody gives."""
        for event, handler in handlers():
            if handler.get("async") or handler.get("asyncRewake"):
                with self.subTest(event=event, command=handler["command"]):
                    self.assertNotIn("onFailure", handler)

    def test_a_gate_whose_library_is_gone_exits_with_neither_answer(self):
        """The case the entry is for. Before `guard` runs there is nobody to turn the failure
        into a refusal: the import fails, Python exits 1, and without the entry the call runs."""
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "bin"
            bare.mkdir()
            shutil.copy2(BIN / "pre-tool", bare / "pre-tool")
            proc = subprocess.run(
                [sys.executable, str(bare / "pre-tool")],
                input=json.dumps({"session_id": "s1", "hook_event_name": "PreToolUse",
                                  "tool_name": "Bash", "tool_input": {"command": "ls"}}),
                capture_output=True, text=True, timeout=60, cwd=tmp,
                # As Claude Code starts it: the sharded runner exports the library on
                # PYTHONPATH, where the import would find it and the gate would run.
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            )
        self.assertNotIn(proc.returncode, (0, 2), proc.stdout + proc.stderr)
        self.assertIn("claude_bestpractice", proc.stderr)


if __name__ == "__main__":
    unittest.main()
