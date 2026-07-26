"""Bytes a real repository legitimately contains, against gates that fail closed.

Every case here wedged a gate permanently. That is the specific severity: not "an error
is reported" but "the gate raises, the raise means refuse, and nothing the founder can
type makes it stop" — no config setting, no re-run, no new session. A repository with one
oddly-named file could not be finished in, ever, and the message was about a codec.

The common cause was treating bytes as text. A POSIX filename is bytes; a source file
from 2011 is bytes; a pasted token is bytes. `text=True` decodes all of them as strict
UTF-8, and strict is the wrong setting for every one.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from helpers import RepoCase, git

# A latin-1 'é'. Undecodable as UTF-8, and entirely ordinary in a European codebase.
LATIN1_NAME = os.fsdecode(b"caf\xe9.py")
LATIN1_SOURCE = b"# caf\xe9 module\ndef f():\n    pass\n"


class TestUndecodableFilenames(RepoCase):
    def make_it(self) -> None:
        path = os.path.join(str(self.repo), LATIN1_NAME)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY))

    def test_git_output_round_trips_to_a_file_that_opens(self):
        """surrogateescape, not replace. The difference is whether the path still works.

        `replace` would substitute U+FFFD, and every downstream existence check would
        then read the file as deleted — which is how a failing suite passed the gate
        once already. The escaped form has to reach the real inode.
        """
        from founder_os import gitctx

        self.make_it()
        changed = gitctx.changed_files(self.ctx())
        self.assertEqual(len(changed), 1, changed)
        self.assertTrue(
            os.path.exists(os.path.join(str(self.repo), changed[0])),
            f"{changed[0]!r} does not name a file on disk",
        )

    def test_the_stop_gate_survives_it(self):
        """It did not. It raised UnicodeDecodeError, and fail-closed turned that into
        a permanent refusal of every finish in the repository."""
        self.make_it()
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo)},
        )
        self.assertNotIn("UnicodeDecodeError", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_the_pre_write_gate_survives_it(self):
        self.make_it()
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "new.py"), "content": "x = 1\n"},
            },
        )
        self.assertNotIn("UnicodeDecodeError", proc.stderr)

    def test_such_a_path_can_still_be_persisted(self):
        """The path is only useful if it survives being written down and read back.

        A lone surrogate cannot be UTF-8 encoded, so storing one raised inside the same
        fail-closed gates — the crash simply moved from the read to the write.
        """
        from founder_os import gitctx, store

        self.make_it()
        ctx = self.ctx()
        changed = gitctx.changed_files(ctx)
        path = store.tier_b(ctx, "surrogates.json")
        store.write_json(path, {"paths": changed})
        self.assertEqual(store.read_json(path)["paths"], changed)

    def test_readable_content_is_not_escaped_to_pay_for_it(self):
        """The fallback must be a fallback. Committed Tier A files are read by humans."""
        from founder_os import store

        path = store.tier_a(self.ctx(), "readable.json")
        store.write_json(path, {"why": "перепробовали три подхода"})
        self.assertIn("перепробовали", path.read_text(encoding="utf-8"))


class TestUndecodableFileContents(RepoCase):
    def test_a_latin1_source_file_does_not_wedge_the_discipline_check(self):
        from founder_os import discipline

        (self.repo / "legacy.py").write_bytes(LATIN1_SOURCE)
        baseline = self.commit("legacy")
        (self.repo / "legacy.py").write_bytes(LATIN1_SOURCE + b"# TODO: later\n")
        self.assertIsInstance(discipline.introduced(self.ctx(), baseline, ["legacy.py"]), list)

    def test_the_stop_gate_survives_a_latin1_file_in_the_diff(self):
        (self.repo / "legacy.py").write_bytes(LATIN1_SOURCE)
        self.commit("legacy")
        (self.repo / "legacy.py").write_bytes(LATIN1_SOURCE + b"x = 2\n")
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "cwd": str(self.repo)},
        )
        self.assertNotIn("UnicodeDecodeError", proc.stderr)

    def test_a_latin1_commit_message_does_not_break_the_ship_view(self):
        """git log output is bytes too, and the founder-facing view reads it."""
        from founder_os import delivery

        self.write("a.py", "x = 1\n")
        git(["add", "-A"], self.repo)
        subprocess.run(
            ["git", "commit", "-q", "-F", "-"],
            cwd=str(self.repo), input=b"ajout de la caf\xe9ti\xe8re", timeout=60,
        )
        self.assertIsInstance(delivery.commits_since(self.ctx(), "HEAD~1"), list)


class TestHostileHookInput(RepoCase):
    def test_a_lone_surrogate_in_tool_input_does_not_refuse_the_call(self):
        """A paste can carry one. The gate is fail-closed, so raising means refusing."""
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "x.py"), "content": "a\udce9b"},
            },
        )
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("UnicodeEncodeError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
