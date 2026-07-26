"""Quality gates for the languages a founder actually ships.

The Python gates are AST-exact and covered nothing for a Next.js frontend against a Go
API — the strongest checks applied to the smallest part of the codebase. These are
regex-based on purpose: an AST per language means a parser per language, and the hard
constraint here is standard library only.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from helpers import REPO_ROOT, RepoCase

TOOL = REPO_ROOT / "tools" / "check_polyglot.py"


class PolyglotCase(RepoCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
            env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )

    def rules_for(self, name: str, source: str) -> set:
        """Rules triggered by THIS file — the scan covers the whole tree, and an earlier
        assertion in the same test leaves its file behind."""
        self.write(name, source)
        return {
            line.split("[", 1)[1].split("]", 1)[0]
            for line in self.run_tool("--all").stdout.splitlines()
            if line.strip().startswith(f"{name}:") and "[" in line
        }


class TestErrorHandling(PolyglotCase):
    def test_an_empty_catch_is_caught_in_typescript(self):
        self.assertIn(
            "swallowed-exception",
            self.rules_for("a.ts", "try {\n  go();\n} catch (e) {}\n"),
        )

    def test_an_empty_promise_catch_is_caught(self):
        self.assertIn(
            "swallowed-exception",
            self.rules_for("a.js", "go().catch(() => {});\n"),
        )

    def test_a_handled_catch_is_not_flagged(self):
        source = "try {\n  go();\n} catch (error) {\n  throw new UpstreamError({ cause: error });\n}\n"
        self.assertNotIn("swallowed-exception", self.rules_for("a.ts", source))

    def test_a_discarded_go_error_is_caught(self):
        self.assertIn("ignored-error", self.rules_for("a.go", "package m\n\nfunc f() {\n\t_ = doThing()\n}\n"))

    def test_rust_unwrap_is_caught(self):
        self.assertIn("unwrap-in-production", self.rules_for("a.rs", "fn f() { x.unwrap(); }\n"))


class TestTypesAndComments(PolyglotCase):
    def test_any_is_caught(self):
        self.assertIn("any-type", self.rules_for("a.ts", "function f(x: any) { return x; }\n"))

    def test_a_deliberate_any_is_allowed(self):
        """An escape hatch that must be typed out is used only where it is meant."""
        source = "function f(x: any // deliberate: third-party callback\n) { return x; }\n"
        self.assertNotIn("any-type", self.rules_for("a.ts", source))

    def test_jsdoc_types_are_caught(self):
        """The annotation already says it; a JSDoc type is a second copy that rots."""
        source = "/**\n * @param {string} name - The name.\n */\nexport function f(name: string) {}\n"
        self.assertIn("derivable-jsdoc", self.rules_for("a.ts", source))

    def test_a_jsdoc_that_says_why_is_allowed(self):
        source = "/**\n * Minor units: floats lose cents at scale.\n */\nexport function f(n: number) {}\n"
        self.assertEqual(self.rules_for("a.ts", source), set())

    def test_narration_comments_are_caught(self):
        self.assertIn("narration-comment", self.rules_for("a.ts", "// Step 1: fetch\nconst x = 1;\n"))

    def test_a_left_in_console_log_is_caught(self):
        self.assertIn("left-in-console", self.rules_for("a.ts", 'console.log("here");\n'))

    def test_an_untracked_todo_is_caught_and_a_tracked_one_is_not(self):
        self.assertIn("bare-todo", self.rules_for("a.ts", "// TODO: later\nconst x = 1;\n"))
        self.assertNotIn("bare-todo", self.rules_for("b.ts", "// TODO(alice): later\nconst x = 1;\n"))


class TestItDoesNotCryWolf(PolyglotCase):
    def test_a_string_that_merely_mentions_a_pattern_is_not_a_finding(self):
        """Blanking string literals is what stops the gate firing on its own error text."""
        source = 'const message = "use console.log for this // TODO";\n'
        self.assertEqual(self.rules_for("a.ts", source), set())

    def test_generated_and_bundled_files_are_skipped(self):
        self.write("app.min.js", "try{go()}catch(e){}\n")
        self.write("types.d.ts", "declare const x: any;\n")
        self.assertEqual(self.run_tool("--all").returncode, 0)

    def test_vendored_trees_are_skipped(self):
        self.write("node_modules/pkg/index.js", "try{go()}catch(e){}\n")
        self.write("vendor/lib.go", "package v\n\nfunc f() {\n\t_ = x()\n}\n")
        self.assertEqual(self.run_tool("--all").returncode, 0)

    def test_clean_code_exits_zero(self):
        self.write("a.ts", "export function total(items: number[]): number {\n  return items.length;\n}\n")
        proc = self.run_tool("--all")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("clean", proc.stdout)

    def test_line_numbers_are_correct(self):
        """A finding at the wrong line is a finding the reader stops trusting."""
        self.write("a.ts", "const a = 1;\nconst b = 2;\ntry {\n  go();\n} catch (e) {}\n")
        out = self.run_tool("--all").stdout
        self.assertIn("a.ts:5", out)


class TestItRunsInTheSuite(unittest.TestCase):
    def test_make_check_includes_it(self):
        """Two check surfaces that disagree is worse than one."""
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("polyglot", makefile.split("check:")[1].split("\n")[0])

    def test_ci_runs_it_too(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        self.assertIn("make polyglot", workflow)


if __name__ == "__main__":
    unittest.main()
