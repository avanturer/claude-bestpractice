"""Auto-drafting decision records from corrections."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from founder_os import drafts


class TestClassification(unittest.TestCase):
    def test_detects_an_explicit_decision(self):
        self.assertEqual(
            drafts.classify("We decided to use Postgres for the ledger after all"), "decision"
        )

    def test_detects_a_rejection(self):
        self.assertEqual(
            drafts.classify("Don't use an ORM here, the query shapes are too irregular"),
            "rejection",
        )

    def test_detects_a_correction(self):
        self.assertEqual(
            drafts.classify("No, not a queue — instead of that, just poll the table"), "correction"
        )

    def test_detects_a_constraint(self):
        self.assertEqual(
            drafts.classify("The export has to be synchronous, the client cannot poll"),
            "constraint",
        )

    def test_ignores_short_turns(self):
        self.assertIsNone(drafts.classify("no"))

    def test_ignores_polite_noise(self):
        """A correction marker without a decision behind it makes the inbox worthless."""
        for noise in ("No thanks, that is fine for now really", "Actually never mind, carry on"):
            with self.subTest(noise=noise):
                self.assertIsNone(drafts.classify(noise))

    def test_ignores_ordinary_instructions(self):
        self.assertIsNone(drafts.classify("Please add a test for the pagination helper"))


class TestExtraction(RepoCase):
    def test_extracts_the_most_recent_first(self):
        turns = [
            "We decided to use SQLite because ops burden matters more than scale here",
            "Don't add a caching layer until we measure something slow",
        ]
        out = drafts.extract(turns, "main", "s1", [])
        self.assertEqual(len(out), 2)
        self.assertIn("caching", out[0].quote)

    def test_caps_per_turn(self):
        turns = [f"We decided thing number {i} because it matters" for i in range(10)]
        self.assertLessEqual(len(drafts.extract(turns, "main", "s1", [])), drafts.MAX_DRAFTS_PER_TURN)

    def test_deduplicates_repeated_wording(self):
        turns = ["We decided to use SQLite for this"] * 4
        self.assertEqual(len(drafts.extract(turns, "main", "s1", [])), 1)

    def test_quote_is_verbatim_not_paraphrased(self):
        turn = "No, not Redis — we already tried that and the ops burden killed us"
        out = drafts.extract([turn], "main", "s1", [])
        self.assertIn("the ops burden killed us", out[0].quote)


class TestInbox(RepoCase):
    def test_record_then_pending(self):
        ctx = self.ctx()
        drafts.record(ctx, drafts.extract(["We decided to ship the CLI first"], "main", "s1", []))
        self.assertEqual(len(drafts.pending(ctx)), 1)

    def test_resolved_drafts_drop_out(self):
        ctx = self.ctx()
        made = drafts.extract(["We decided to ship the CLI first"], "main", "s1", [])
        drafts.record(ctx, made)
        drafts.resolve(ctx, made[0].quote)
        self.assertEqual(drafts.pending(ctx), [])

    def test_render_puts_the_quote_under_why(self):
        draft = {"quote": "we already tried that", "created_at": 0, "subject_paths": []}
        rendered = drafts.render(draft)
        self.assertIn("## Why", rendered)
        self.assertIn("> we already tried that", rendered)
        self.assertIn("## Rejected", rendered)

    def test_next_number_increments(self):
        ctx = self.ctx()
        self.assertEqual(drafts.next_number(ctx), 1)
        self.write(".claude/rules/decisions/0007-x.md", "---\ntitle: X\npaths: '**'\n---\n")
        self.assertEqual(drafts.next_number(ctx), 8)


class TestTranscriptReading(RepoCase):
    def transcript(self, records: list[dict]) -> str:
        path = self.tmp / "t.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return str(path)

    def test_reads_user_turns(self):
        path = self.transcript(
            [
                {"type": "user", "message": {"content": "first thing"}},
                {"type": "assistant", "message": {"content": "reply"}},
                {"type": "user", "message": {"content": [{"type": "text", "text": "second"}]}},
            ]
        )
        self.assertEqual(drafts.user_turns(path), ["first thing", "second"])

    def test_skips_sidechain_turns(self):
        path = self.transcript(
            [{"type": "user", "isSidechain": True, "message": {"content": "subagent noise"}}]
        )
        self.assertEqual(drafts.user_turns(path), [])

    def test_missing_transcript_is_not_fatal(self):
        """The format is internal and changes between releases. Degrade, never raise."""
        self.assertEqual(drafts.user_turns("/nonexistent/x.jsonl"), [])

    def test_garbage_lines_are_skipped(self):
        path = self.tmp / "t.jsonl"
        path.write_text('{"type":"user","message":{"content":"ok"}}\nnot json at all\n')
        self.assertEqual(drafts.user_turns(str(path)), ["ok"])


class TestCli(RepoCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "founder-os-decide"), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def test_list_is_empty_initially(self):
        proc = self.run_cli("list")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no pending drafts", proc.stdout)

    def test_accept_writes_a_record_and_clears_the_draft(self):
        ctx = self.ctx()
        drafts.record(
            ctx, drafts.extract(["We decided to use SQLite because ops matter"], "main", "s1", [])
        )
        proc = self.run_cli("accept", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        from founder_os import knowledge

        files = knowledge.decision_files(ctx)
        self.assertEqual(len(files), 1)
        self.assertIn("We decided to use SQLite", files[0].read_text())
        self.assertEqual(drafts.pending(ctx), [])

    def test_discard_clears_without_writing(self):
        ctx = self.ctx()
        drafts.record(ctx, drafts.extract(["We decided to drop the queue"], "main", "s1", []))
        self.assertEqual(self.run_cli("discard", "1").returncode, 0)
        self.assertEqual(drafts.pending(ctx), [])

        from founder_os import knowledge

        self.assertEqual(knowledge.decision_files(ctx), [])

    def test_out_of_range_index_is_refused(self):
        self.assertEqual(self.run_cli("accept", "9").returncode, 1)


class TestGateIntegration(RepoCase):
    def test_stop_gate_harvests_drafts(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "No, not Redis — we already tried that and it broke"},
                }
            )
            + "\n"
        )
        for gate, event in (
            ("session-start", {"hook_event_name": "SessionStart"}),
            (
                "evidence-gate",
                {
                    "hook_event_name": "Stop",
                    "stop_hook_active": False,
                    "transcript_path": str(transcript),
                },
            ),
        ):
            subprocess.run(
                [sys.executable, str(BIN / gate)],
                input=json.dumps({"session_id": "s1", "cwd": str(self.repo), **event}),
                capture_output=True,
                text=True,
                cwd=str(self.repo),
                timeout=120,
            )
        pending = drafts.pending(self.ctx())
        self.assertEqual(len(pending), 1)
        self.assertIn("already tried that", pending[0]["quote"])


if __name__ == "__main__":
    unittest.main()
