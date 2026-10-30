"""What an audit of a founder's machine found the plugin had left there (2.6.0, Fuddy, 30.10).

A red record a 1.72.0 gate wrote on the first of the month still told every session started in
the main checkout "failing for 29d … Fix it before new work", over code 103 commits behind; and a
session start died on a card a sibling had moved since it was read.
"""

from __future__ import annotations

import json
import os
import time

from helpers import RepoCase

from claude_bestpractice import evidence, plan, store


class TestARedRecordNobodyConfirmed(RepoCase):
    def record(self, seen: float, tree: str) -> None:
        store.write_json(store.tier_a(self.ctx(), evidence.RED_SUITE_FILE), {
            "command": ["pytest"], "tree_hash": tree, "first_seen": seen, "last_seen": seen,
            "branch": "main", "gate": "1.72.0"}, mode=0o644)

    def test_a_month_old_failure_on_other_code_is_history_to_rerun(self):
        self.record(time.time() - 29 * 86_400, "0" * 40)
        said = evidence.red_line(self.ctx())
        self.assertIn("unconfirmed", said)
        self.assertIn("Run it once", said)
        self.assertNotIn("Fix it before new work", said)

    def test_a_failure_on_this_very_tree_still_stops_new_work(self):
        self.record(time.time() - 29 * 86_400, evidence.tree_hash(self.ctx()))
        self.assertIn("Fix it before new work", evidence.red_line(self.ctx()))

    def test_a_fresh_failure_still_stops_new_work(self):
        self.record(time.time() - 600, "0" * 40)
        self.assertIn("Fix it before new work", evidence.red_line(self.ctx()))


class TestACardASiblingMoved(RepoCase):
    def test_a_sweep_skips_a_card_a_sibling_moved_after_it_was_read(self):
        ctx = self.ctx()
        card = plan.add(ctx, "wire the OTA banner", paths=["app/home.tsx"], done_when="shown")
        stale = plan.find(ctx, card.id)
        moved_by_sibling = stale.path.parent.parent / plan.PAUSED / stale.path.name
        moved_by_sibling.parent.mkdir(parents=True, exist_ok=True)
        stale.path.rename(moved_by_sibling)
        self.assertIsNone(plan._move_if_there(stale, plan.DONE))
        self.assertTrue(moved_by_sibling.exists(), "the sibling's move stands")

    def test_a_claim_still_reads_a_moved_card_again(self):
        ctx = self.ctx()
        card = plan.add(ctx, "a card on the move", paths=["x.py"], done_when="stated")
        plan.pause(ctx, card.id, "waiting")
        claimed, error = plan.claim(ctx, card.id, "s1", "main")
        self.assertEqual("", error)
        self.assertEqual(plan.DOING, claimed.state)


class TestWhatGrewWithoutLimit(RepoCase):
    def test_the_decision_inbox_is_collapsed_past_its_size(self):
        from claude_bestpractice import drafts

        ctx = self.ctx()
        path = store.tier_b(ctx, drafts.INBOX_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"marker": "always", "quote": "never push to main " + "x" * 200, "branch": "main",
               "session_id": "s1", "created_at": 1.0, "subject_paths": [], "seen": 1}
        with path.open("w", encoding="utf-8") as handle:
            for n in range(1_400):
                handle.write(json.dumps({**row, "seen": n + 1}) + "\n")
            handle.write("{broken line\n")
        self.assertGreater(path.stat().st_size, drafts.COMPACT_BYTES)
        drafts.record(ctx, [drafts.Draft(marker="always", quote="another rule the founder gave",
                                         branch="main", session_id="s1", created_at=time.time(),
                                         subject_paths=[])])
        self.assertLess(path.stat().st_size, 4_096)
        self.assertEqual(2, len(drafts.pending(ctx)))

    def test_tokens_of_killed_runs_are_let_go(self):
        ctx = self.ctx()
        stale = store.tier_b(ctx, evidence.NONCE_DIR, "a" * 32)
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("")
        old = time.time() - evidence.NONCE_LIFETIME - 60
        os.utime(stale, (old, old))
        fresh = store.tier_b(ctx, evidence.NONCE_DIR, "b" * 32)
        fresh.write_text("")
        self.assertEqual(1, evidence.retire_stale_nonces(ctx))
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_the_inbox_of_a_gone_session_is_dropped_and_a_live_ones_kept(self):
        from claude_bestpractice import inbox, sessions

        ctx = self.ctx()
        live = self.session_record("live-session")
        sessions.register(ctx, live)
        box = store.tier_b(ctx, inbox.DIRNAME)
        box.mkdir(parents=True, exist_ok=True)
        old = [{"text": "a fact", "created_at": time.time() - inbox.RETENTION_SECONDS - 60}]
        store.write_json(box / "gone-session.json", old)
        store.write_json(box / f"{sessions.safe_id('live-session')}.json", old)
        self.assertEqual(1, inbox.drop_unread_boxes(ctx))
        self.assertFalse((box / "gone-session.json").exists())
        self.assertTrue((box / f"{sessions.safe_id('live-session')}.json").exists())


class TestTheCheckpointKeepsWhatTheFounderSaid(RepoCase):
    def test_only_a_persons_messages_are_read_and_only_from_the_end(self):
        from helpers import queued, turn

        from claude_bestpractice import founder

        transcript = self.tmp / "t.jsonl"
        tool_result = {"type": "user", "isSidechain": False, "timestamp": "2026-10-30T10:00:00Z",
                       "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": "x", "content": "3 passed"}]}}
        lines = [turn("an old request " + "x" * 2_000, time.time() - 60),
                 tool_result, turn("fix the OTA banner", time.time() - 30),
                 queued("+merge after the checks", time.time())]
        transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        said = founder.messages(str(transcript), most_bytes=1_200)
        self.assertEqual(["fix the OTA banner", "+merge after the checks"], said)
