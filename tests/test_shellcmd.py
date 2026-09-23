"""A gate decides on what a line RUNS, not on what it contains.

Issue #76: every command whose text held the merge invocation was refused as a merge —
`echo` of it, `grep` for it in documentation, a script carrying it as a JSON payload. So
reporting on the gate, quoting it, or searching for it all became impossible, and the tool
for investigating the gate was blocked by the gate.
"""

from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import tempfile
import sys
import time
import unittest
import uuid
from unittest import mock

from helpers import BIN, RepoCase

from claude_bestpractice import ci, gitpolicy, pullrequest, shellcmd, vouch


class TestReadingIsNotDoing(unittest.TestCase):
    def merges(self, line: str) -> bool:
        return pullrequest.merge_target("Bash", line, {}) is not None

    def test_the_real_invocation_is_still_a_merge(self):
        self.assertTrue(self.merges("gh pr merge 461 --admin --squash"))
        self.assertEqual(461, pullrequest.merge_target("Bash", "gh pr merge 461", {}))

    def test_talking_about_it_is_not(self):
        for line in (
            "echo 'the report said: gh pr merge 461'",
            "grep -rn 'gh pr merge' docs/",
            "cat CHANGELOG.md | grep -c 'gh pr merge'",
            "printf '%s\\n' 'gh pr merge'",
        ):
            self.assertFalse(self.merges(line), line)

    def test_it_survives_the_ways_a_command_is_really_written(self):
        for line in (
            "cd /tmp && gh pr merge",
            "timeout 30 gh pr merge 5",
            "nice -n 5 gh pr merge",
            "GH_TOKEN=x gh pr merge 7",
            "/usr/bin/gh pr merge",
        ):
            self.assertTrue(self.merges(line), line)

    def test_looking_a_program_up_is_not_running_it(self):
        self.assertFalse(self.merges("command -v gh"))

    def test_an_unparseable_line_falls_back_rather_than_through(self):
        """A line crafted to break the tokeniser must not become one that walks past."""
        self.assertEqual([], shellcmd.commands('echo "unbalanced'))
        self.assertTrue(self.merges('gh pr merge 3 "unbalanced'))

    def test_readers_are_dropped_from_the_acting_set(self):
        self.assertEqual([], shellcmd.acting("echo 'deploy --prod'"))
        self.assertEqual([["fly", "deploy", "--prod"]], shellcmd.acting("fly deploy --prod"))


class TestTheSameShapeOneGateOver(RepoCase):
    """`PRODUCTION_DEPLOY` matched flags anywhere in the line. Not reported — nobody had
    tried to write about a deploy yet — and found by looking for the shape #76 describes."""

    def setUp(self) -> None:
        super().setUp()
        # The migration gate is off below `traction`, so a prototype fixture proves
        # nothing about it either way — the first version of this test asserted a refusal
        # that had never been switched on.
        self.configure(stage_override="traction")

    def bash(self, command: str) -> str:
        proc = subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input=json.dumps({
                "cwd": str(self.repo), "session_id": "s1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": command},
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
        except (json.JSONDecodeError, KeyError, TypeError):
            return "allow"

    def test_writing_about_a_production_deploy_is_not_one(self):
        self.assertNotEqual("deny", self.bash("echo 'we deploy with --production'"))

    def test_actually_deploying_is_still_refused(self):
        self.assertEqual("deny", self.bash("railway up"))

    def test_the_flag_handed_to_something_else_is_not_a_deploy(self):
        """`--prod` anywhere in a line was a promotion, so an install, a search, a commit
        message, and `plan` and `get` against production all waited on the founder."""
        for command in (
            "npm ci --production",
            "pnpm install --prod",
            "git grep -n -- --production",
            'git commit -m "Install only production deps with npm ci --production"',
            "terraform plan -var environment=production",
            "kubectl get pods --context prod-eu",
        ):
            self.assertNotEqual("deny", self.bash(command), command)

    def test_every_real_promotion_is_still_refused(self):
        for command in (
            "vercel --prod",
            "npx vercel deploy --prod",
            "netlify deploy --prod",
            "kubectl --context=prod-eu rollout restart deploy/api",
            "kubectl set image deploy/api api=api:2 --context production",
            "terraform apply -var environment=production",
            "helm upgrade api ./chart --kube-context prod",
            "eas update --branch production",
            "(cd web && vercel --prod)",
            "URL=$(vercel --prod)",
            "./scripts/deploy.sh --production",
            "npm run deploy -- --prod",
        ):
            self.assertEqual("deny", self.bash(command), command)

    def test_the_founders_word_is_spent_only_on_a_real_promotion(self):
        """`+release` allows one promotion, and a search that merely mentioned the flag
        spent it — so the promotion the founder had approved was refused after all."""
        self.run_hook("prompt-capture", {
            "session_id": "s1", "hook_event_name": "UserPromptSubmit",
            "prompt": "checked the preview\n+release",
        })
        self.assertNotEqual("deny", self.bash("git grep -n -- --production"))
        self.assertNotEqual("deny", self.bash("vercel --prod"))
        self.assertEqual("deny", self.bash("vercel --prod"), "one word allowed two promotions")


class TestALineTheShellWillNotParse(unittest.TestCase):
    """A dangling `&&` used to make a line parse SHORTER, not fail.

    `make test &&` became `make test`, which `vouch` approves — and `allow_tool` ends the
    permission pipeline, so the approval landed ahead of Claude Code 2.1.246's own rule
    that a malformed command always requires approval. The bar is bash: what it rejects,
    this rejects.
    """

    MALFORMED = ("make test &&", "make test ||", "make test |", "make test |&",
                 "&& make test", "|| make test", "make test && && ls",
                 "cd x && ruff check &&", "a ;; b", "a ;& b", "a ;;& b")
    WELL_FORMED = ("make test", "ruff check src/ ;", "sleep 1 &", "a && b", "a | b",
                   "a; b", "echo 'a && b'", 'echo "x || y"', "cd x && ruff check")

    def test_bash_agrees_these_are_malformed(self):
        """The list above is checked against the shell, not against our opinion of it."""
        for line in self.MALFORMED:
            proc = subprocess.run(["bash", "-nc", line], capture_output=True)
            self.assertNotEqual(0, proc.returncode, f"bash accepts {line!r}")

    def test_bash_agrees_these_are_fine(self):
        for line in self.WELL_FORMED:
            proc = subprocess.run(["bash", "-nc", line], capture_output=True)
            self.assertEqual(0, proc.returncode, f"bash rejects {line!r}")

    def test_a_malformed_line_yields_nothing_to_judge(self):
        for line in self.MALFORMED:
            self.assertEqual([], shellcmd.segments(line), f"{line!r} parsed anyway")
            self.assertEqual([], shellcmd.commands(line), f"{line!r} parsed anyway")

    def test_a_valid_terminator_is_not_a_malformation(self):
        """`;` and `&` legally end a line. Refusing them would cost real work to fix nothing."""
        for line in self.WELL_FORMED:
            self.assertTrue(shellcmd.segments(line), f"{line!r} was refused")

    def test_a_quoted_operator_is_still_text(self):
        """The whole reason this module exists — `echo 'a && b'` runs one command."""
        self.assertEqual([["echo", "a && b"]], shellcmd.segments("echo 'a && b'"))


class TestALineIsReadOnceAndInLinearTime(unittest.TestCase):
    """Every gate asked its own question of the same Bash line and each tokenised it afresh —
    eleven readings of one `git commit` — while `shlex` built each word by copying it whole
    per character. A 256k-character commit message took the hook eleven seconds."""

    # Quotes of both kinds, escapes in and out of them, adjacent quoted parts, operators,
    # redirections, a comment, a heredoc, non-ASCII and an empty word.
    LINES = (
        "git commit -m 'Handle \"quoted\" fields' && git push",
        'echo "a \\"b\\" c \\$HOME \\\\ d" | grep -c x; ls',
        "echo a'b'\"c\"d '' \"\" x\\ y  >out.txt 2>&1",
        "cat > notes.md <<'EOF'\nline one\nline 'two'\nEOF",
        "echo 'café — привет' # a comment\npwd",
        "grep -rn \"open('config.json', 'w')\" src/ || true",
    )

    def stock_words(self, line: str) -> list[str]:
        lexer = shlex.shlex(line, posix=True, punctuation_chars="();<>|&`")
        lexer.whitespace_split = True
        return [word for word in lexer if word not in ("&&", "||", ";", "|", "&", "\n")]

    def test_the_words_are_the_ones_the_stock_lexer_reads(self):
        """Growing a word in place changes how long it takes, never what it is."""
        for line in self.LINES:
            with self.subTest(line=line):
                read = [word for argv in shellcmd.segments(line) for word in argv]
                self.assertEqual(self.stock_words(line), read)

    def test_a_line_the_stock_lexer_refuses_is_still_refused(self):
        with self.assertRaises(ValueError):
            self.stock_words("echo \"it's")
        self.assertEqual([], shellcmd.segments("echo \"it's"))

    def test_every_gate_shares_one_reading_of_the_line(self):
        line = f"git commit -m 'read once {uuid.uuid4().hex}' && gh pr create --title t --body b"
        readings = []
        original = shlex.shlex.__init__

        def counting(lexer, *args, **kwargs):
            readings.append(args[:1])
            original(lexer, *args, **kwargs)

        with mock.patch.object(shlex.shlex, "__init__", counting):
            pullrequest.merge_target("Bash", line, {})
            pullrequest.opens_a_pull_request("Bash", line)
            gitpolicy.stages_everything(line)
            gitpolicy.changes_the_repository(line)
            gitpolicy.commit_message(line)
            ci.verbs_run(line)
            vouch.own_command(line)
        self.assertEqual(1, len(readings), "the line was read once per question")

    def test_a_shlex_that_builds_its_word_another_way_still_reads_the_line(self):
        """The word is grown in place by standing in for `shlex`'s own attribute. A Python
        whose `read_token` asks that attribute something new must cost speed, never a gate
        that fails closed on every Bash call."""
        marker = uuid.uuid4().hex
        with mock.patch.object(shellcmd._Token, "__iadd__", side_effect=TypeError):
            self.assertEqual([["git", "log", marker]], shellcmd.segments(f"git log '{marker}'"))

    def test_what_one_caller_does_to_its_copy_is_not_what_the_next_reads(self):
        marker = uuid.uuid4().hex
        handed = shellcmd.segments(f"ls src {marker}")
        handed[0].append("--pre=rm")
        handed.append(["rm", "-rf", "src"])
        self.assertEqual([["ls", "src", marker]], shellcmd.segments(f"ls src {marker}"))

    def test_one_long_quoted_argument_is_read_in_linear_time(self):
        """Measured where this was written: 5.1 s through the stock lexer, 0.23 s grown in place."""
        body = "x" * 600_000
        started = time.monotonic()
        parsed = shellcmd.segments(f"git commit -m '{body}'")
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual([["git", "commit", "-m", body]], parsed)


