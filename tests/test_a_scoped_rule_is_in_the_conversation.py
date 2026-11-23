"""Decisions 0113 and 0114: a scoped rule is in the conversation before the act it governs, and a
founder's layer is scoped line for line.

The gate is proven through the real `pre-tool`, `checkpoint` and `claude-bp-mod` as the harness
and the hooks module start them. The probe is proven against a stand-in `claude` that writes the
transcript a real headless session leaves.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import BIN, RepoCase, git

from claude_bestpractice import rulescope

DESIGN = """---
paths:
  - "mobile/**"
  - "landing/*.{ts,tsx}"
---

# Design

## Один визуал на ВСЕ платформы — железно

Every screen looks the same on iOS and Android.
"""

RELEASING = """---
paths: ["mobile/app.json"]
commands:
  - "make mobile-ota"
  - "eas (build|submit)"
---

# Releasing

See `.claude/rules/design.md` → «Один визуал
на ВСЕ платформы» before a store build.
"""


class ScopeCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / "rules").mkdir(parents=True)
        patched = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)})
        patched.start()
        self.addCleanup(patched.stop)
        self.write(".claude/rules/design.md", DESIGN)
        self.write(".claude/rules/releasing.md", RELEASING)
        self.write(".claude/rules/everywhere.md", "# Everywhere\n\nAlways say what you did.\n")
        self.write("mobile/app.json", "{}\n")
        self.write("mobile/app/home.tsx", "export {}\n")
        self.write("landing/page.tsx", "export {}\n")
        self.commit("rules")

    def env(self) -> dict:
        return {**os.environ, "CLAUDE_CONFIG_DIR": str(self.home)}

    def mod(self, asked: str, **request) -> dict:
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-mod"), asked],
                              input=json.dumps({"cwd": str(self.repo), "session": "h1", **request}),
                              capture_output=True, text=True, env=self.env(), timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout or "{}")

    def call(self, tool: str, given: dict, agent: str = ""):
        event = {"session_id": "h1", "hook_event_name": "PreToolUse", "tool_name": tool,
                 "tool_input": given}
        if agent:
            event["agent_id"] = agent
        return self.run_hook("pre-tool", event, env=self.env())

    def write_new(self, relpath: str, agent: str = ""):
        return self.call("Write", {"file_path": str(self.repo / relpath), "content": "x\n"}, agent)


class TestReadingTheScope(unittest.TestCase):
    def test_the_three_spellings_rule_files_use(self) -> None:
        self.assertEqual(("mobile/**", "landing/*.{ts,tsx}"), rulescope.field(DESIGN, "paths"))
        self.assertEqual(("mobile/app.json",), rulescope.field(RELEASING, "paths"))
        self.assertEqual(("make mobile-ota", "eas (build|submit)"),
                         rulescope.field(RELEASING, "commands"))
        record = "---\ntitle: x\npaths: a/b.py, 'c/*.md'\n---\n"
        self.assertEqual(("a/b.py", "c/*.md"), rulescope.field(record, "paths"))
        self.assertEqual((), rulescope.field("# no frontmatter\npaths: x\n", "paths"))

    def test_globs_as_claude_code_reads_them(self) -> None:
        cases = [("mobile/**", "mobile/app/home.tsx", True), ("mobile/**", "mobilex/a", False),
                 ("backend/**/*.py", "backend/a.py", True), ("backend/**/*.py", "backend/x/y/a.py", True),
                 ("backend/**/*.py", "backend/a.pyc", False), ("src/*.{ts,tsx}", "src/a.tsx", True),
                 ("src/*.{ts,tsx}", "src/x/a.ts", False), ("MEMORY.md", "MEMORY.md", True),
                 ("**/memory/**", "a/memory/b.md", True), ("a/?.md", "a/b.md", True)]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                rule = rulescope.Rule(Path("r.md"), "r.md", (pattern,), ())
                self.assertIs(expected, rulescope.matches(rule, path))
        dotted = rulescope.Rule(Path("r.md"), "r.md", (".claude/skills/**",), ())
        self.assertTrue(rulescope.matches(dotted, ".claude/skills/x/SKILL.md"))
        # A directory named covers what is under it, as Claude Code loads decision 0102 (`plugin/bin`)
        # beside `plugin/bin/pre-tool`.
        folder = rulescope.Rule(Path("r.md"), "r.md", ("plugin/bin",), ())
        self.assertTrue(rulescope.matches(folder, "plugin/bin/pre-tool"))
        self.assertFalse(rulescope.matches(folder, "plugin/binary"))


class TestTheGate(ScopeCase):
    def test_silent_until_the_module_says_it_runs(self) -> None:
        self.claim_a_task("h1", "mobile/app/new.tsx")
        proc = self.write_new("mobile/app/new.tsx")
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_a_new_file_waits_once_for_its_rule(self) -> None:
        self.mod("rules-heard")
        self.claim_a_task("h1", "mobile/app/new.tsx")
        first = self.write_new("mobile/app/new.tsx")
        self.assertEqual("deny", self.hook_decision(first))
        reason = self.hook_reason(first)
        self.assertIn(".claude/rules/design.md", reason)
        self.assertIn(str(self.repo / ".claude/rules/design.md"), reason)
        self.assertNotIn("releasing.md", reason)
        second = self.write_new("mobile/app/new.tsx")
        self.assertNotEqual("deny", self.hook_decision(second), self.hook_reason(second))

    def test_a_rule_the_module_saw_attached_is_not_asked_for(self) -> None:
        self.mod("rules-heard")
        said = self.mod("rules-loaded", loop="",
                        text=f"Contents of {self.repo / '.claude/rules/design.md'}:\n# Design")
        self.assertEqual([".claude/rules/design.md"], said["loaded"])
        self.claim_a_task("h1", "mobile/app/new.tsx")
        proc = self.write_new("mobile/app/new.tsx")
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))

    def test_a_rule_named_inside_another_is_not_taken_as_loaded(self) -> None:
        self.mod("rules-heard")
        said = self.mod("rules-loaded", loop="", text=f"Contents of {self.repo}/x.md:\n"
                                                      "see .claude/rules/design.md first")
        self.assertEqual([], said["loaded"])

    def test_the_rules_own_words_count_whatever_the_framing(self) -> None:
        self.mod("rules-heard")
        said = self.mod("rules-loaded", loop="", text="# Design\n\n## Один визуал на ВСЕ платформы — "
                        "железно\n\nEvery screen looks the same on iOS and Android.")
        self.assertEqual([".claude/rules/design.md"], said["loaded"])

    def test_a_load_reported_after_clear_turns_the_gate_on(self) -> None:
        # `/clear` gives a new session id and no `session.start` to announce the module.
        self.mod("rules-loaded", loop="", path=str(self.repo / ".claude/rules/releasing.md"))
        self.claim_a_task("h1", "mobile/app/new.tsx")
        self.assertEqual("deny", self.hook_decision(self.write_new("mobile/app/new.tsx")))

    def test_a_command_is_named_as_the_command_not_its_redirect(self) -> None:
        self.mod("rules-heard")
        proc = self.call("Bash", {"command": "make mobile-ota > ota.log"})
        self.assertEqual("deny", self.hook_decision(proc))
        reason = self.hook_reason(proc)
        self.assertIn("governs this command", reason)
        self.assertNotIn("governs `ota.log`", reason)

    def test_reading_the_rule_file_counts(self) -> None:
        self.mod("rules-heard")
        self.mod("rules-loaded", loop="", path=str(self.repo / ".claude/rules/design.md"))
        self.claim_a_task("h1", "mobile/app/new.tsx")
        self.assertNotEqual("deny", self.hook_decision(self.write_new("mobile/app/new.tsx")))

    def test_a_subagent_has_its_own_rules(self) -> None:
        self.mod("rules-heard")
        self.mod("rules-loaded", loop="", path=str(self.repo / ".claude/rules/design.md"))
        self.claim_a_task("h1", "mobile/app/new.tsx")
        proc = self.write_new("mobile/app/new.tsx", agent="agent-7")
        self.assertEqual("deny", self.hook_decision(proc))

    def test_compaction_asks_again(self) -> None:
        self.mod("rules-heard")
        self.mod("rules-loaded", loop="", path=str(self.repo / ".claude/rules/design.md"))
        proc = self.run_hook("checkpoint", {"session_id": "h1", "hook_event_name": "PreCompact",
                                            "trigger": "auto", "transcript_path": ""},
                             env=self.env())
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.claim_a_task("h1", "mobile/app/new.tsx")
        self.assertEqual("deny", self.hook_decision(self.write_new("mobile/app/new.tsx")))

    def test_a_command_waits_for_its_rule(self) -> None:
        self.mod("rules-heard")
        proc = self.call("Bash", {"command": "cd mobile && eas build --platform ios"})
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn(".claude/rules/releasing.md", self.hook_reason(proc))
        other = self.call("Bash", {"command": "git status"})
        self.assertNotEqual("deny", self.hook_decision(other), self.hook_reason(other))

    def test_off_means_off(self) -> None:
        self.configure(rules_in_context=False)
        self.commit("off")
        self.mod("rules-heard")
        self.claim_a_task("h1", "mobile/app/new.tsx")
        self.assertNotEqual("deny", self.hook_decision(self.write_new("mobile/app/new.tsx")))

    def test_the_founders_own_rules_are_asked_for_too(self) -> None:
        (self.home / "rules" / "ui.md").write_text('---\npaths: ["landing/**"]\n---\n# UI\n')
        self.mod("rules-heard")
        self.mod("rules-loaded", loop="", path=str(self.repo / ".claude/rules/design.md"))
        self.claim_a_task("h1", "landing/new.tsx")
        proc = self.write_new("landing/new.tsx")
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("~/.claude/rules/ui.md", self.hook_reason(proc))


class TestCheckingALayer(ScopeCase):
    def test_what_is_wrong_with_the_scoping_is_named(self) -> None:
        self.write(".claude/rules/dead.md", '---\npaths: ["nowhere/**"]\n---\n# Dead\n')
        self.write(".claude/rules/huge.md", "---\npaths: [\"mobile/**\"]\n---\n" + "x" * 40_100)
        self.write(".claude/rules/bare.md", "---\ncommands: [\"deploy\"]\n---\n# Bare\n")
        self.write("CLAUDE.md", "Read `.claude/rules/gone.md` and .claude/rules/design.md → «Нет такого»\n")
        self.commit("problems")
        found = "\n".join(rulescope.problems(self.ctx()))
        self.assertIn("`nowhere/**` matches no tracked file", found)
        self.assertIn("huge.md: 40,", found)
        self.assertIn("bare.md: `commands:` without `paths:`", found)
        self.assertIn("names `.claude/rules/gone.md`, which is not there", found)
        self.assertIn("«Нет такого»", found)
        # A reference wrapped across lines still finds its heading.
        self.assertNotIn("releasing.md: points to", found)

    def test_a_reference_to_the_founders_own_rules_is_not_the_projects(self) -> None:
        self.write("CLAUDE.md", "Mine: `~/.claude/rules/personal.md`, /abs/.claude/rules/x.md\n")
        self.commit("refs")
        self.assertEqual([], [p for p in rulescope.problems(self.ctx()) if "CLAUDE.md" in p])

    def test_a_section_moved_to_a_nested_claude_md_is_not_dropped(self) -> None:
        self.write("CLAUDE.md", "# Root\n\nBackend answers in under 200 ms.\n")
        base = self.commit("before")
        self.write("CLAUDE.md", "# Root\n")
        self.write("backend/CLAUDE.md", "# Root\n\nBackend answers in under 200 ms.\n")
        self.commit("after")
        moves = rulescope.compare(self.ctx(), base)
        self.assertEqual([], moves.dropped)
        self.assertIn(("CLAUDE.md", "backend/CLAUDE.md", "Backend answers in under 200 ms.", False),
                      moves.scope_changed)

    def test_a_revision_that_names_nothing_is_an_error_not_a_pass(self) -> None:
        cli = [sys.executable, str(BIN / "claude-bp"), "rules", "--since", "mian"]
        proc = subprocess.run(cli, cwd=self.repo, capture_output=True, text=True, env=self.env())
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("names no commit", proc.stderr)

    def test_an_import_from_outside_the_tree_is_counted_not_a_crash(self) -> None:
        (self.tmp / "shared.md").write_text("shared rules\n")
        self.write("CLAUDE.md", "See @../shared.md\n")
        self.commit("import")
        _total, files = rulescope.cost(self.ctx())
        self.assertTrue(any(name.endswith("shared.md") for name, _size in files), files)
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp"), "rules"], cwd=self.repo,
                              capture_output=True, text=True, env=self.env())
        self.assertNotIn("Traceback", proc.stderr)

    def test_every_rule_loaded_at_launch_is_counted(self) -> None:
        _total, files = rulescope.cost(self.ctx())
        self.assertIn(".claude/rules/everywhere.md", [name for name, _size in files])
        self.assertNotIn(".claude/rules/design.md", [name for name, _size in files])

    def test_every_line_is_found_again_or_named(self) -> None:
        self.write(".claude/rules/preferences.md",
                   "# Prefs\n\n## PR flow\n\nNever push to main.\n\n## Screens\n\n"
                   "Text is never cut off on a screen.\nButtons are 44 points tall.\n")
        base = self.commit("before")
        self.write(".claude/rules/preferences.md", "# Prefs\n\n## PR flow\n\nNever push to main.\n")
        self.write(".claude/rules/visual-qa.md",
                   "---\npaths:\n  - \"mobile/**\"\n---\n\nText is never cut off on a screen.\n")
        self.commit("after")
        moves = rulescope.compare(self.ctx(), base)
        # The heading the moved line sat under went with nobody, and is named as gone too.
        self.assertEqual([(".claude/rules/preferences.md", "## Screens"),
                          (".claude/rules/preferences.md", "Buttons are 44 points tall.")],
                         moves.dropped)
        self.assertIn((".claude/rules/preferences.md", ".claude/rules/visual-qa.md",
                       "Text is never cut off on a screen.", True), moves.scope_changed)
        self.assertIn((".claude/rules/preferences.md", ".claude/rules/visual-qa.md",
                       "Text is never cut off on a screen."), moves.lost_heading)

    def test_a_file_that_gains_paths_is_a_scope_change_for_every_line(self) -> None:
        self.write(".claude/rules/alerts.md", "# Alerts\n\nNever page Rodion at night.\n")
        base = self.commit("before")
        self.write(".claude/rules/alerts.md",
                   "---\npaths: [\"mobile/**\"]\n---\n# Alerts\n\nNever page Rodion at night.\n")
        self.commit("after")
        moves = rulescope.compare(self.ctx(), base)
        self.assertIn((".claude/rules/alerts.md", ".claude/rules/alerts.md",
                       "Never page Rodion at night.", True), moves.scope_changed)
        self.assertEqual([], moves.dropped)

    def test_the_command_fails_on_a_dropped_line_until_the_founder_agrees(self) -> None:
        base = git(["rev-parse", "HEAD"], self.repo)
        self.write(".claude/rules/everywhere.md", "# Everywhere\n")
        self.commit("drop")
        cli = [sys.executable, str(BIN / "claude-bp"), "rules", "--since", base]
        refused = subprocess.run(cli, cwd=self.repo, capture_output=True, text=True, env=self.env())
        self.assertEqual(1, refused.returncode, refused.stdout)
        self.assertIn("Always say what you did.", refused.stdout)
        agreed = subprocess.run(cli + ["--accept-dropped"], cwd=self.repo, capture_output=True,
                                text=True, env=self.env())
        self.assertEqual(0, agreed.returncode, agreed.stdout + agreed.stderr)

    def test_memory_past_what_loads_is_named(self) -> None:
        key = "".join(c if c.isalnum() else "-" for c in str(self.repo.resolve()))
        index = self.home / "projects" / key / "memory" / "MEMORY.md"
        index.parent.mkdir(parents=True)
        index.write_text("- fact\n" * 10)
        self.assertEqual("", rulescope.memory_overflow(self.ctx()))
        index.write_text("- a fact with a long description\n" * 900)
        self.assertIn("reaches no session", rulescope.memory_overflow(self.ctx()))
        _total, files = rulescope.cost(self.ctx())
        # The first 200 lines load, which here is under 25 KB.
        loaded = len("- a fact with a long description\n") * rulescope.MEMORY_LIMIT_LINES
        self.assertIn(("MEMORY.md (auto memory, as loaded)", loaded), files)
        # With auto memory off, as on the founder's machine, none of it loads.
        (self.home / "settings.json").write_text('{"env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}}')
        self.assertEqual("", rulescope.memory_overflow(self.ctx()))
        self.assertNotIn("MEMORY.md (auto memory, as loaded)",
                         [name for name, _size in rulescope.cost(self.ctx())[1]])

    def test_tokens_are_counted_from_bytes(self) -> None:
        self.assertEqual(16_446, rulescope.tokens(58_385))


@unittest.skipIf(os.name == "nt", "the stand-in claude is a POSIX script")
class TestTheProbe(ScopeCase):
    def a_claude(self, attaches: bool) -> Path:
        """A headless `claude` that leaves a transcript, with the rule in it or not."""
        rule = self.repo / ".claude/rules/design.md"
        key = "".join(c if c.isalnum() else "-" for c in str(self.repo.resolve()))
        body = f"Contents of {rule}" if attaches else "no rule"
        script = self.tmp / "claude"
        script.write_text(
            f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
            f"folder = pathlib.Path({str(self.home)!r}) / 'projects' / {key!r}\n"
            "folder.mkdir(parents=True, exist_ok=True)\n"
            f"text = {body!r} + ' after ' + sys.argv[2]\n"
            "(folder / 'sess-1.jsonl').write_text(json.dumps({'type': 'attachment', 'text': text}))\n"
            "print(json.dumps({'session_id': 'sess-1', 'result': 'done'}))\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_a_rule_found_in_the_transcript_is_loaded(self) -> None:
        verdicts = rulescope.probe(self.ctx(), only="design", claude=str(self.a_claude(True)))
        self.assertEqual([(".claude/rules/design.md", "landing/page.tsx", "loaded")], verdicts)

    def test_a_rule_missing_from_it_is_not(self) -> None:
        verdicts = rulescope.probe(self.ctx(), only="design", claude=str(self.a_claude(False)))
        self.assertEqual("not loaded", verdicts[0][2])

    def test_no_claude_is_said(self) -> None:
        verdicts = rulescope.probe(self.ctx(), only="design", claude=str(self.tmp / "absent"))
        self.assertIn("not on PATH", verdicts[0][2])


if __name__ == "__main__":
    unittest.main()
