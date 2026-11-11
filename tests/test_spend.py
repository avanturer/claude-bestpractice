"""`claude-bp spend`: what sessions spent, read off transcripts whose answer is known.

Every transcript here is written by hand, so each number the report gives can be worked out
on paper: a request's context is what it read, wrote and sent fresh, and its spend is those
at list-price ratios.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import BIN, RepoCase

from claude_bestpractice import spend, store


def line(ident: str, read: int = 0, written: int = 0, out: int = 0, fresh: int = 0,
         hour: bool = True, **extra) -> str:
    """One transcript line of a reply, as Claude Code writes it."""
    usage = {"input_tokens": fresh, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": written, "output_tokens": out,
             "cache_creation": {"ephemeral_1h_input_tokens": written if hour else 0,
                                "ephemeral_5m_input_tokens": 0 if hour else written}}
    return json.dumps({"type": "assistant", "message": {
        "id": ident, "model": "claude-fable-5-2", "usage": usage}, **extra})


def growing(requests: int, floor: int = 50_000, step: int = 10_000) -> list[str]:
    """A conversation that starts at `floor` and adds `step` on every request."""
    lines = [line("m0", written=floor)]
    lines += [line(f"m{n}", read=floor + step * (n - 1), written=step, out=100)
              for n in range(1, requests)]
    return lines


class TranscriptCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)

    def transcript(self, lines: list[str], name: str = "session.jsonl") -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


class TestARequestIsCountedOnce(TranscriptCase):
    def test_a_reply_written_as_three_lines_is_one_request(self):
        """One line per block of a reply, each under the same message id and usage."""
        path = self.transcript([line("m1", read=1000, out=10)] * 3 + [line("m2", read=2000)])
        self.assertEqual([1000, 2000], [turn.read for turn in spend.turns(path)])

    def test_what_is_no_request_is_left_out(self):
        path = self.transcript([
            json.dumps({"type": "user", "message": {"content": "the usage of it"}}),
            json.dumps({"type": "assistant", "message": {
                "id": "s", "model": "<synthetic>", "usage": {"input_tokens": 9}}}),
            "not json at all, with \"usage\" in it",
            line("m1", read=500),
        ])
        self.assertEqual([500], [turn.read for turn in spend.turns(path)])

    def test_a_usage_is_priced_by_what_it_says(self):
        path = self.transcript([line("m1", read=1000, written=200, out=30, fresh=4),
                                line("m2", written=200, hour=False)])
        first, second = spend.turns(path)
        self.assertEqual(1204, first.context)
        self.assertEqual(4 + 200 * 2.0 + 1000 * 0.1 + 30 * 5.0, first.spend)
        self.assertEqual(200 * 1.25, second.spend)

    def test_a_subagents_requests_are_its_own(self):
        own = self.transcript([line("m1", read=100)])
        agent = self.transcript([line("a1", read=100)], "subagents/agent-1.jsonl")
        report = spend.measure([own, agent])
        self.assertEqual((2, 1), (report.requests, report.by_agents))

    def test_a_subagents_conversation_is_replayed_under_the_ceiling_too(self):
        agent = self.transcript([json.dumps({**json.loads(text), "isSidechain": True})
                                 for text in growing(60)], "subagents/agent-1.jsonl")
        report = spend.measure([agent])
        self.assertGreater(report.compactions[200_000], 0)
        self.assertLess(report.replayed[200_000], report.replayed[0])


class TestTheReplayIsCheckedAgainstWhatWasSpent(TranscriptCase):
    def test_with_no_ceiling_it_gives_what_was_spent(self):
        history = spend.turns(self.transcript(growing(40)))
        spent, compactions = spend.replay(history, 0)
        self.assertAlmostEqual(sum(turn.spend for turn in history), spent)
        self.assertEqual(0, compactions)

    def test_a_ceiling_nothing_reached_changes_nothing(self):
        history = spend.turns(self.transcript(growing(10)))
        self.assertEqual(spend.replay(history, 0), spend.replay(history, 400_000))

    def test_a_ceiling_compacts_and_what_is_read_after_it_is_less(self):
        """50k growing by 10k a request: past 200k on the seventeenth, where the context
        falls back to the floor and what a compaction leaves."""
        history = spend.turns(self.transcript(growing(60)))
        whole, _ = spend.replay(history, 0)
        capped, compactions = spend.replay(history, 200_000)
        self.assertGreater(compactions, 0)
        self.assertLess(capped, whole)
        every = (200_000 - 50_000 - spend.KEPT) // 10_000
        self.assertAlmostEqual(compactions, (60 - 16) // every + 1, delta=1)

    def test_a_conversation_compacted_in_fact_is_followed_down(self):
        lines = growing(30) + [line("after", read=50_000, written=20_000, out=100)]
        history = spend.turns(self.transcript(lines))
        spent, _ = spend.replay(history, 0)
        self.assertAlmostEqual(sum(turn.spend for turn in history), spent)


class TestWhatTheReportSays(TranscriptCase):
    def test_spend_is_split_by_kind_and_by_how_full_the_context_was(self):
        report = spend.measure([self.transcript(growing(60))])
        self.assertAlmostEqual(report.spend, sum(report.kinds.values()))
        self.assertAlmostEqual(report.spend, sum(report.bands.values()))
        self.assertGreater(report.bands[(400_000, 0)], report.bands[(0, 100_000)])
        self.assertEqual([50_000], report.floors)
        self.assertEqual([50_000 + 59 * 10_000], report.peaks)

    def test_a_prefix_written_again_mid_session_is_counted(self):
        lines = growing(20) + [line("back", read=10_000, written=230_000, out=50)]
        report = spend.measure([self.transcript(lines)])
        self.assertEqual((1, 230_000), (report.rewrites, report.rewritten))

    def test_it_reads_as_a_page(self):
        said = spend.render(spend.measure([self.transcript(growing(60))]), (0, ""))
        self.assertIn("1 conversation(s), a subagent's among them, 60 requests", said)
        self.assertIn("at 200k:", said)
        self.assertIn("Ceiling in effect here: none", said)
        self.assertIn("autoCompactWindow", said)

    def test_nothing_found_says_so(self):
        self.assertIn("No requests", spend.render(spend.measure([]), (0, "")))


class TestWhereItLooks(RepoCase):
    def home(self) -> Path:
        home = self.repo.parent / "home"
        (home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
        return home

    def project(self, home: Path, where: Path, name: str = "s.jsonl") -> Path:
        key = "".join(char if char.isalnum() else "-" for char in where.as_posix())
        path = home / ".claude" / "projects" / key / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line("m1", read=1000) + "\n", encoding="utf-8")
        return path

    def test_the_checkouts_sessions_and_its_trees_and_nobody_elses(self):
        home = self.home()
        mine = self.project(home, self.repo)
        tree = self.project(home, self.repo / ".claude" / "worktrees" / "feat-x")
        beside = self.project(home, self.repo.parent / f"{self.repo.name}-topic")
        self.project(home, self.repo.parent / "another-project")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": ""}):
            found = spend.transcripts(self.ctx(), home=home)
        self.assertEqual(sorted([mine, tree, beside]), sorted(found))

    def test_only_the_days_asked_for(self):
        home = self.home()
        old = self.project(home, self.repo, "old.jsonl")
        new = self.project(home, self.repo, "new.jsonl")
        long_ago = time.time() - 40 * 86400
        os.utime(old, (long_ago, long_ago))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": ""}):
            self.assertEqual([new], spend.transcripts(self.ctx(), days=14, home=home))

    def test_the_ceiling_is_read_where_claude_code_reads_it(self):
        home = self.home()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": ""}):
            self.assertEqual((0, ""), spend.ceiling_in_effect(self.ctx(), home))
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"autoCompactWindow": 400000}), encoding="utf-8")
            self.assertEqual(400_000, spend.ceiling_in_effect(self.ctx(), home)[0])
            self.write(".claude/settings.json", json.dumps({"autoCompactWindow": "300k"}))
            size, where = spend.ceiling_in_effect(self.ctx(), home)
            self.assertEqual(300_000, size)
            self.assertTrue(where.endswith(".claude/settings.json"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000"}):
            self.assertEqual((200_000, "CLAUDE_CODE_AUTO_COMPACT_WINDOW"),
                             spend.ceiling_in_effect(self.ctx(), home))

    def test_the_command_prints_the_report(self):
        home = self.home()
        self.project(home, self.repo)
        env = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": "",
               "CLAUDE_CODE_AUTO_COMPACT_WINDOW": ""}
        out = subprocess.run([sys.executable, str(BIN / "claude-bp"), "spend", "--days", "0"],
                             cwd=self.repo, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("1 conversation(s), a subagent's among them, 1 requests", out.stdout)


class TestOldCheckpointsGo(RepoCase):
    def checkpoint(self, name: str, age_days: float = 0) -> Path:
        path = store.checkpoint_dir(self.ctx()) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# checkpoint\n", encoding="utf-8")
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
        return path

    def test_a_sessions_older_checkpoints_go_and_its_newest_stays(self):
        older = self.checkpoint("20261101-100000-aaaaaaaa.md", 2)
        newest = self.checkpoint("20261102-100000-aaaaaaaa.md", 1)
        anothers = self.checkpoint("20261101-090000-bbbbbbbb.md", 2)

        removed = store.prune_checkpoints(self.ctx(), "aaaaaaaa-1111")

        self.assertEqual(1, removed)
        self.assertEqual([False, True, True],
                         [path.exists() for path in (older, newest, anothers)])
        self.assertIn("checkpoint", store.newest_checkpoint(self.ctx(), "aaaaaaaa-1111"))

    def test_a_checkpoint_nobody_came_back_to_for_a_month_goes(self):
        stale = self.checkpoint("20260901-090000-cccccccc.md", 45)
        mine_and_stale = self.checkpoint("20260901-080000-aaaaaaaa.md", 45)

        store.prune_checkpoints(self.ctx(), "aaaaaaaa-1111")

        self.assertFalse(stale.exists())
        self.assertTrue(mine_and_stale.exists())

    def test_the_hook_prunes_as_it_writes(self):
        self.run_hook("session-start", {"session_id": "aaaaaaaa-1111",
                                        "hook_event_name": "SessionStart", "source": "startup"})
        older = self.checkpoint("20261101-100000-aaaaaaaa.md", 2)
        self.run_hook("checkpoint", {"session_id": "aaaaaaaa-1111",
                                     "hook_event_name": "PreCompact", "trigger": "auto"})
        left = sorted(path.name for path in store.checkpoint_dir(self.ctx()).glob("*.md"))
        self.assertFalse(older.exists())
        self.assertEqual(1, len(left), left)


if __name__ == "__main__":
    unittest.main()
