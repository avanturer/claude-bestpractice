"""`plugin/hooks/hooks.json` carries only keys Claude Code reads.

Claude Code 2.1.329 names every key of it that it does not read at each session start, on the
founder's machine: `bestpractice: hooks.json: unknown keys "$comment", "$comment" in
hooks.SessionStart[0], … ignored`, five of them. `claude plugin validate --strict` passes the same
file, so `make manifests` cannot see it. The notes those keys held are
`.claude/rules/hooks-json.md`, which Claude Code hands a session working on the file.
"""

from __future__ import annotations

import json
import unittest
from typing import Iterator

from helpers import REPO_ROOT

HOOKS = REPO_ROOT / "plugin" / "hooks" / "hooks.json"
NOTES = REPO_ROOT / ".claude" / "rules" / "hooks-json.md"


def keys(node: object, where: str = "") -> Iterator[tuple[str, str]]:
    """Every key in `node`, with where it sits as Claude Code's warning names it."""
    if isinstance(node, dict):
        for key, value in node.items():
            at = f"{where}.{key}" if where else key
            yield at, key
            yield from keys(value, at)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from keys(value, f"{where}[{index}]")


class TestTheHooksFileHoldsOnlyWhatClaudeCodeReads(unittest.TestCase):
    def test_no_key_is_a_note(self):
        notes = [at for at, key in keys(json.loads(HOOKS.read_text(encoding="utf-8")))
                 if key.startswith("$")]
        self.assertEqual([], notes, "named at every session start: put it in "
                                    ".claude/rules/hooks-json.md instead")

    def test_its_notes_are_handed_to_a_session_working_on_it(self):
        head = NOTES.read_text(encoding="utf-8").split("---")[1]
        self.assertRegex(head, r"(?m)^paths:\s*plugin/hooks/hooks\.json\s*$")


if __name__ == "__main__":
    unittest.main()
