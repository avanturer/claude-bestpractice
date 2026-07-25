"""Provenance stamping and staleness — the mechanism nobody else ships."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase

from founder_os import board, provenance


class TestStamping(RepoCase):
    def test_stamp_records_a_blob_per_path(self):
        self.write("a.py", "x = 1\n")
        self.write("b.py", "y = 2\n")
        stamps = provenance.stamp(self.ctx(), ["a.py", "b.py"])
        self.assertEqual([s["path"] for s in stamps], ["a.py", "b.py"])
        self.assertTrue(all(len(s["blob"]) == 40 for s in stamps))

    def test_identical_content_hashes_identically(self):
        self.write("a.py", "same\n")
        self.write("b.py", "same\n")
        stamps = provenance.stamp(self.ctx(), ["a.py", "b.py"])
        self.assertEqual(stamps[0]["blob"], stamps[1]["blob"])

    def test_missing_paths_are_skipped_not_fatal(self):
        self.write("a.py", "x = 1\n")
        stamps = provenance.stamp(self.ctx(), ["a.py", "gone.py"])
        self.assertEqual([s["path"] for s in stamps], ["a.py"])

    def test_empty_input_is_safe(self):
        self.assertEqual(provenance.stamp(self.ctx(), []), [])


class TestDriftDetection(RepoCase):
    def test_unchanged_content_is_fresh(self):
        self.write("a.py", "x = 1\n")
        stamps = provenance.stamp(self.ctx(), ["a.py"])
        status, changed = provenance.check(self.ctx(), stamps)
        self.assertEqual(status, provenance.FRESH)
        self.assertEqual(changed, [])

    def test_edited_content_is_suspect(self):
        self.write("a.py", "x = 1\n")
        stamps = provenance.stamp(self.ctx(), ["a.py"])
        self.write("a.py", "x = 2\n")
        status, changed = provenance.check(self.ctx(), stamps)
        self.assertEqual(status, provenance.SUSPECT)
        self.assertEqual(changed, ["a.py"])

    def test_deleted_subject_is_gone(self):
        self.write("a.py", "x = 1\n")
        stamps = provenance.stamp(self.ctx(), ["a.py"])
        (self.repo / "a.py").unlink()
        status, _ = provenance.check(self.ctx(), stamps)
        self.assertEqual(status, provenance.GONE)

    def test_partial_deletion_is_suspect_not_gone(self):
        self.write("a.py", "x = 1\n")
        self.write("b.py", "y = 2\n")
        stamps = provenance.stamp(self.ctx(), ["a.py", "b.py"])
        (self.repo / "a.py").unlink()
        status, changed = provenance.check(self.ctx(), stamps)
        self.assertEqual(status, provenance.SUSPECT)
        self.assertIn("a.py", changed)

    def test_touching_a_file_without_changing_it_is_not_drift(self):
        """The failure mode of every mtime-based scheme, and the reason for blobs."""
        self.write("a.py", "x = 1\n")
        stamps = provenance.stamp(self.ctx(), ["a.py"])
        future = time.time() + 10_000
        os.utime(self.repo / "a.py", (future, future))
        status, _ = provenance.check(self.ctx(), stamps)
        self.assertEqual(status, provenance.FRESH)

    def test_a_worktree_checkout_does_not_invalidate_everything(self):
        """Creating a worktree resets mtimes; content-addressing must survive it."""
        self.write("a.py", "x = 1\n")
        self.commit()
        stamps = provenance.stamp(self.ctx(), ["a.py"])

        from founder_os.gitctx import resolve

        wt_ctx = resolve(self.add_worktree("feature"))
        status, _ = provenance.check(wt_ctx, stamps)
        self.assertEqual(status, provenance.FRESH)

    def test_unstamped_claim_is_fresh(self):
        """Missing provenance is our failure to record, not the claim's fault."""
        status, _ = provenance.check(self.ctx(), [])
        self.assertEqual(status, provenance.FRESH)

    def test_malformed_stamp_is_ignored(self):
        status, _ = provenance.check(self.ctx(), [{"nonsense": True}])
        self.assertEqual(status, provenance.FRESH)


class TestAnnotation(RepoCase):
    def test_annotate_tags_each_claim(self):
        self.write("a.py", "x = 1\n")
        claims = [{"id": "1", "subject_paths": provenance.stamp(self.ctx(), ["a.py"])}]
        self.write("a.py", "x = 2\n")
        out = provenance.annotate(self.ctx(), claims)
        self.assertEqual(out[0]["provenance"], provenance.SUSPECT)
        self.assertEqual(out[0]["provenance_changed"], ["a.py"])

    def test_summarize_counts_by_status(self):
        self.write("a.py", "x = 1\n")
        self.write("b.py", "y = 1\n")
        fresh = {"subject_paths": provenance.stamp(self.ctx(), ["b.py"])}
        stale = {"subject_paths": provenance.stamp(self.ctx(), ["a.py"])}
        self.write("a.py", "x = 2\n")
        counts = provenance.summarize(provenance.annotate(self.ctx(), [fresh, stale]))
        self.assertEqual(counts[provenance.FRESH], 1)
        self.assertEqual(counts[provenance.SUSPECT], 1)


class TestBoardIntegration(RepoCase):
    def record(self):
        from founder_os.sessions import SessionRecord

        ctx = self.ctx()
        return SessionRecord(
            session_id="s1",
            pid=os.getpid(),
            worktree=ctx.worktree_root.as_posix(),
            branch=ctx.branch,
            baseline_commit=ctx.head,
            started_at=time.time(),
            heartbeat_at=time.time(),
        )

    def test_fresh_item_appears_on_the_board(self):
        ctx = self.ctx()
        self.write("a.py", "x = 1\n")
        board.add_open_item(ctx, "item-1", "fix the parser", ctx.branch, "s1", ["a.py"])
        rendered = board.render(ctx, self.record(), [], 0)
        self.assertIn("fix the parser", rendered)

    def test_stale_item_is_suppressed_but_counted(self):
        """Suppressed, not deleted: the founder can see knowledge exists and needs repair."""
        ctx = self.ctx()
        self.write("a.py", "x = 1\n")
        board.add_open_item(ctx, "item-1", "fix the parser", ctx.branch, "s1", ["a.py"])
        self.write("a.py", "completely rewritten\n")

        rendered = board.render(ctx, self.record(), [], 0)
        self.assertNotIn("fix the parser", rendered)
        self.assertIn("1 stale (suppressed)", rendered)

    def test_item_without_subjects_survives(self):
        ctx = self.ctx()
        board.add_open_item(ctx, "item-1", "no subjects here", ctx.branch, "s1")
        self.assertIn("no subjects here", board.render(ctx, self.record(), [], 0))


class TestCheckpointStamps(RepoCase):
    def test_checkpoint_records_blob_hashes(self):
        subprocess.run(
            [sys.executable, str(BIN / "session-start")],
            input=json.dumps(
                {"session_id": "s1", "hook_event_name": "SessionStart", "cwd": str(self.repo)}
            ),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=60,
        )
        self.write("feature.py", "x = 1\n")
        subprocess.run(
            [sys.executable, str(BIN / "checkpoint")],
            input=json.dumps(
                {
                    "session_id": "s1",
                    "hook_event_name": "PreCompact",
                    "cwd": str(self.repo),
                    "trigger": "auto",
                }
            ),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=60,
        )
        text = next((self.repo / ".claude" / "founder-os" / "checkpoints").glob("*.md")).read_text()
        self.assertIn("feature.py @ ", text)


if __name__ == "__main__":
    unittest.main()
