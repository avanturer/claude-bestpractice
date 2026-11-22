"""Decision 0112: after an edit, the project's own linter is asked about the lines it wrote.

The linters here are stand-ins kept where the project would keep the real ones (`.venv/bin`,
`node_modules/.bin`) and printing the JSON each real one prints: ruff 0.16's array with
`location.row`, ESLint 10's results with `messages`. The suite runs where neither is installed;
one test asks a real ruff where there is one.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import unittest

from helpers import RepoCase, harness_matches

from claude_bestpractice import config, editcheck

RUFF_SAYS = [
    {"code": "F401", "message": "`os` imported but unused", "location": {"row": 1, "column": 8}},
    {"code": "E711", "message": "Comparison to `None`", "location": {"row": 3, "column": 6}},
]
ESLINT_SAYS = [{"filePath": "x", "messages": [
    {"ruleId": "no-unused-vars", "severity": 2, "message": "'a' is never used", "line": 2,
     "column": 7}]}]


def a_patch(start: int, *lines: str) -> dict:
    """An edit's result as Claude Code hands PostToolUse: one hunk from `start`."""
    return {"type": "update", "structuredPatch": [
        {"oldStart": start, "newStart": start, "oldLines": 0, "newLines": 0, "lines": list(lines)}]}


@unittest.skipIf(os.name == "nt", "the stand-in linters are POSIX scripts")
class EditLintCase(RepoCase):
    def a_linter(self, where: str, says: list, argv_to: str = "") -> None:
        """A program at `where` that prints `says` as JSON and, given `argv_to`, writes there
        the arguments it was started with."""
        record = f"open({argv_to!r}, 'w').write(json.dumps(sys.argv[1:]))\n" if argv_to else ""
        script = self.write(where, f"#!{sys.executable}\nimport json, sys\n{record}"
                                   f"print(json.dumps({says!r}))\nsys.exit(1)\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def edited(self, relpath: str, response: dict | None = None, tool: str = "Edit"):
        return self.run_hook("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_use_id": "toolu_e1",
            "tool_input": {"file_path": str(self.repo / relpath)},
            "tool_response": response if response is not None else a_patch(
                1, "+import os", " x = 1", "-if x == None:", "+if x == None: pass")})

    def context(self, proc) -> str:
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout or "{}").get("hookSpecificOutput", {}).get(
            "additionalContext", "")

    def test_the_findings_on_the_lines_the_edit_wrote_reach_the_session(self) -> None:
        self.write("pyproject.toml", "[tool.ruff]\nline-length = 100\n")
        self.write("app.py", "import os\nx = 1\nif x == None: pass\n")
        self.a_linter(".venv/bin/ruff", RUFF_SAYS)
        said = self.context(self.edited("app.py"))
        self.assertIn("ruff finds 2 problem(s) on the lines this edit wrote in app.py", said)
        self.assertIn("1:8  F401  `os` imported but unused", said)
        self.assertIn("3:6  E711", said)

    def test_a_finding_on_a_line_the_edit_did_not_write_is_not_said(self) -> None:
        self.write("ruff.toml", "")
        self.write("app.py", "import os\nx = 1\nif x == None: pass\n")
        self.a_linter(".venv/bin/ruff", RUFF_SAYS)
        said = self.context(self.edited("app.py", a_patch(3, "-if x is None: pass",
                                                          "+if x == None: pass")))
        self.assertIn("1 problem(s)", said)
        self.assertNotIn("F401", said)

    def test_the_linter_is_asked_to_write_nothing(self) -> None:
        self.write("ruff.toml", "")
        self.write("app.py", "x = 1\n")
        argv = self.tmp / "argv.json"
        self.a_linter(".venv/bin/ruff", [], argv_to=str(argv))
        self.context(self.edited("app.py"))
        asked = json.loads(argv.read_text())
        self.assertEqual("check", asked[0])
        self.assertIn("--no-fix", asked)
        self.assertNotIn("--fix", asked)

    def test_eslint_is_the_projects_own_and_only_under_a_flat_config(self) -> None:
        self.write("web/eslint.config.mjs", "export default [];\n")
        self.write("web/src/a.ts", "const x = 1;\nconst a = 1;\n")
        self.a_linter("web/node_modules/.bin/eslint", ESLINT_SAYS)
        said = self.context(self.edited("web/src/a.ts", tool="Write",
                                        response={"type": "create", "structuredPatch": []}))
        self.assertIn("eslint finds 1 problem(s) on the lines this edit wrote in web/src/a.ts", said)
        self.assertIn("2:7  no-unused-vars", said)

    def test_nothing_is_run_where_the_project_configures_no_linter(self) -> None:
        self.write("app.py", "import os\n")
        argv = self.tmp / "argv.json"
        self.a_linter(".venv/bin/ruff", RUFF_SAYS, argv_to=str(argv))
        self.assertEqual("", self.context(self.edited("app.py")))
        self.assertFalse(argv.exists())

    def test_a_linter_the_project_did_not_install_is_not_fetched(self) -> None:
        self.write("eslint.config.js", "export default [];\n")
        self.write("a.js", "const a = 1;\n")
        self.assertEqual("", self.context(self.edited("a.js")))

    def test_off_hands_the_edit_back_as_it_was(self) -> None:
        self.configure(lint_on_edit=False)
        self.write("ruff.toml", "")
        self.write("app.py", "import os\n")
        self.a_linter(".venv/bin/ruff", RUFF_SAYS)
        self.assertEqual("", self.context(self.edited("app.py")))
        self.assertEqual({"lint_on_edit": "off"}, config.switches_in("lint_on_edit off"))

    def test_a_linter_that_prints_no_json_is_silence(self) -> None:
        self.write("ruff.toml", "")
        self.write("app.py", "import os\n")
        script = self.write(".venv/bin/ruff", "#!/bin/sh\necho 'error: bad config' >&2\nexit 2\n")
        script.chmod(0o755)
        self.assertEqual("", self.context(self.edited("app.py")))

    def test_the_list_is_bounded(self) -> None:
        self.write("ruff.toml", "")
        self.write("app.py", "x = 1\n" * 40)
        many = [{"code": "E1", "message": "m", "location": {"row": row, "column": 1}}
                for row in range(1, 31)]
        self.a_linter(".venv/bin/ruff", many)
        said = self.context(self.edited("app.py", {"type": "create"}))
        self.assertIn("30 problem(s)", said)
        self.assertIn(f"and {30 - editcheck.MOST} more.", said)

    @unittest.skipUnless(shutil.which("ruff"), "no ruff on this machine")
    def test_a_real_ruff_is_read(self) -> None:
        self.write("pyproject.toml", "[tool.ruff.lint]\nselect = [\"F\"]\n")
        self.write("app.py", "import os\nx = 1\n")
        said = self.context(self.edited("app.py", a_patch(1, "+import os", " x = 1")))
        self.assertIn("F401", said)
        self.assertEqual("import os\nx = 1\n", (self.repo / "app.py").read_text())


class TouchedLinesCase(unittest.TestCase):
    def test_added_lines_are_counted_from_the_hunks_start(self) -> None:
        self.assertEqual({11, 12}, editcheck.touched(a_patch(10, " a", "+b", "+c", " d")))

    def test_a_removed_line_marks_the_line_now_in_its_place(self) -> None:
        self.assertEqual({11}, editcheck.touched(a_patch(10, " a", "-b", " c")))

    def test_an_empty_line_in_a_hunk_is_context(self) -> None:
        self.assertEqual({12}, editcheck.touched(a_patch(10, " a", "", "+c")))

    def test_the_no_newline_marker_is_no_line(self) -> None:
        self.assertEqual({11}, editcheck.touched(a_patch(10, " a", "+b", "\\ No newline at end of file")))

    def test_a_created_file_and_a_missing_patch_are_every_line(self) -> None:
        self.assertIsNone(editcheck.touched({"type": "create", "structuredPatch": []}))
        self.assertIsNone(editcheck.touched({}))

    def test_an_identical_write_touched_nothing(self) -> None:
        self.assertEqual(set(), editcheck.touched({"type": "update", "structuredPatch": []}))

    def test_a_hunk_without_its_lines_is_its_range(self) -> None:
        self.assertEqual({4, 5, 6}, editcheck.touched(
            {"structuredPatch": [{"newStart": 4, "newLines": 3}]}))


class WiringCase(unittest.TestCase):
    def test_the_edit_tools_reach_the_entry_pr_opened_already_has(self) -> None:
        from helpers import REPO_ROOT

        hooks = json.loads((REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text())
        entries = [entry for entry in hooks["hooks"]["PostToolUse"]
                   if any("pr-opened" in hook["command"] and "if" not in hook
                          for hook in entry["hooks"])]
        self.assertEqual(1, len(entries))
        for tool in editcheck.EDIT_TOOLS:
            self.assertTrue(harness_matches(entries[0]["matcher"], tool), tool)
        self.assertFalse(harness_matches(entries[0]["matcher"], "NotEditing"))


if __name__ == "__main__":
    unittest.main()
