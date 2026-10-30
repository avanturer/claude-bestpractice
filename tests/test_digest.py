"""A long successful result reaches the model as a digest, and the whole of it stays on disk.

Measured on a real project: a passing `pytest -v` of 10,153 tests printed 2.5 MB, and what a
reader acts on in it is one summary line, ten warnings and where the rest is (decision 0048).
The fixtures here are built in the test, never copied out of somebody's project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from helpers import BIN, RepoCase, sid

from claude_bestpractice import board, config, digest, outputs, plan, sessions

PASSING = "".join(f"tests/test_mod{n % 40}.py::test_case_{n} PASSED{' ' * 20}[{n % 100:3d}%]\n"
                  for n in range(4_000))
VERBOSE_RUN = ("============================= test session starts ==============================\n"
               "platform linux -- Python 3.13.16, pytest-9.2.0\n" + PASSING
               + "tests/test_mod3.py::test_case_3\n"
                 "  /src/api.py:270: DeprecationWarning: 'HTTP_422' is deprecated.\n"
               + "======= 4000 passed, 2 skipped, 1 warning in 97.88s (0:01:37) =======\n")


class TestWhatIsDigested(RepoCase):
    def test_suites_builds_and_installs_are_told_apart_from_reads(self):
        for line, kind in (("pytest -v", digest.TESTS), ("uv run pytest -q", digest.TESTS),
                           ("python -m pytest tests/", digest.TESTS), ("npx jest --ci", digest.TESTS),
                           ("npm test", digest.TESTS), ("make test", digest.TESTS),
                           ("cd backend && go test ./...", digest.TESTS),
                           ("npm ci", digest.BUILD), ("pip install -r requirements.txt", digest.BUILD),
                           ("npm run build", digest.BUILD), ("make build", digest.BUILD),
                           ("cat log.txt", digest.READ), ("git log -p", digest.READ),
                           ("rg TODO src", digest.READ), ("pytest -q | grep FAILED", digest.READ),
                           ("./scripts/deploy.sh", digest.OTHER)):
            self.assertEqual(kind, digest.classify(line), line)

    def test_a_command_the_founder_declared_is_a_suite_whatever_its_name(self):
        self.assertEqual(digest.TESTS, digest.classify("./ci.sh all", (("./ci.sh", "all"),)))


class TestTheDigest(RepoCase):
    def test_a_passing_verbose_run_keeps_its_verdict_and_its_warning(self):
        body = digest.condense(VERBOSE_RUN, digest.TESTS)
        self.assertIsNotNone(body)
        self.assertLessEqual(len(body), digest.BODY_CHARS)
        self.assertIn("4000 passed, 2 skipped, 1 warning", body)
        self.assertIn("DeprecationWarning", body)
        self.assertIn("of them a test passing", body)

    def test_an_output_it_cannot_shrink_enough_is_left_whole(self):
        """Every line of it an error: a digest keeping most of them saves too little."""
        lines = "".join(f"error[E{n:04d}]: mismatched types in src/lib{n}.rs\n" for n in range(40))
        self.assertIsNone(digest.condense(lines, digest.BUILD))


class TestTheAnswerTheModuleGets(RepoCase):
    def ask(self, command: str, text: str, **extra) -> dict:
        return outputs.answer(self.ctx(), {"command": command, "stdout": text, "stderr": "", **extra})

    def test_a_long_passing_suite_is_answered_with_a_digest_and_its_whole_kept(self):
        said = self.ask("pytest -v", VERBOSE_RUN)
        first, _, body = said["stdout"].partition("\n")
        kept = first.split("the whole output is in ")[1].split(" — ")[0]
        self.assertIn("4000 passed", body)
        self.assertEqual(VERBOSE_RUN, open(kept, encoding="utf-8").read())
        self.assertIn("/.claude/claude-bestpractice/out/", kept)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                                capture_output=True, text=True).stdout
        self.assertNotIn("out/", status, "a kept output is uncommitted work to every git check")

    def test_what_a_session_asked_to_read_is_never_touched(self):
        self.assertEqual({}, self.ask("cat pytest.log", VERBOSE_RUN))

    def test_another_command_is_left_alone_until_claude_code_would_cut_it(self):
        self.assertEqual({}, self.ask("./scripts/report.sh", VERBOSE_RUN))
        persisted = self.tmp / "persisted.txt"
        persisted.write_text(VERBOSE_RUN, encoding="utf-8")
        said = self.ask("./scripts/report.sh", VERBOSE_RUN[:2_000], persisted=str(persisted))
        self.assertIn(str(persisted), said["stdout"].split("\n", 1)[0])
        self.assertFalse((self.repo / ".claude" / "claude-bestpractice" / "out").exists(),
                         "Claude Code's own file is named, not copied")

    def test_the_founder_can_switch_it_off(self):
        self.configure(compress_output=False)
        self.assertEqual({}, self.ask("pytest -v", VERBOSE_RUN))

    def test_old_outputs_are_let_go(self):
        home = self.repo / ".claude" / "claude-bestpractice" / "out"
        home.mkdir(parents=True)
        for n in range(outputs.KEPT + 5):
            stale = home / f"2026010{n:03d}.log"
            stale.write_text("x", encoding="utf-8")
            os.utime(stale, (time.time() - n, time.time() - n))
        self.ask("pytest -v", VERBOSE_RUN)
        self.assertEqual(outputs.KEPT, len(list(home.glob("*.log"))))


class TestTheDoorTheModuleAsksThrough(RepoCase):
    def door(self, asked: str, request: dict) -> dict:
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-mod"), asked],
                              input=json.dumps({"cwd": str(self.repo), **request}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def test_a_digest_comes_back_as_json(self):
        said = self.door("digest", {"command": "pytest -v", "stdout": VERBOSE_RUN, "session": "s1"})
        self.assertIn("4000 passed", said["stdout"])

    def test_the_settings_say_what_the_founder_left_on(self):
        self.configure(route_search=False, subagent_min_model="opus")
        said = self.door("settings", {})
        self.assertEqual({"enabled": True, "compress_output": True, "route_search": False,
                          "subagent_min_model": "opus"}, said)

    def test_with_the_plugin_off_it_changes_nothing(self):
        self.configure(enabled=False)
        self.assertEqual({}, self.door("digest", {"command": "pytest -v", "stdout": VERBOSE_RUN}))
        self.assertFalse(self.door("settings", {})["route_search"])


class TestWhoIsDoingWhat(RepoCase):
    def test_a_listing_names_each_other_session_with_its_card(self):
        ctx = self.ctx()
        mine, theirs = sid(self.repo, "harness-me"), sid(self.repo, "harness-them")
        for session_id, name in ((mine, "repo-me"), (theirs, "repo-them")):
            rec = self.session_record(session_id)
            rec.name = name
            sessions.register(ctx, rec)
        card = plan.add(ctx, "OTA banner on the home screen", paths=["app/home.tsx"], done_when="shown")
        plan.claim(ctx, card.id, theirs, ctx.branch)
        said = board.roster(ctx, "harness-me")
        self.assertIn("repo-them:", said)
        self.assertIn("OTA banner on the home screen", said)
        self.assertNotIn("repo-me:", said)

    def test_alone_it_says_nothing(self):
        rec = self.session_record(sid(self.repo, "harness-me"))
        sessions.register(self.ctx(), rec)
        self.assertEqual("", board.roster(self.ctx(), "harness-me"))


class TestTheSwitches(RepoCase):
    def test_the_founder_switches_them_by_saying_so(self):
        self.assertEqual({"compress_output": "off", "route_search": "off"},
                         config.switches_in("compress_output off\nroute_search off"))
