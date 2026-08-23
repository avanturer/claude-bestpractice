"""Delivering a fact into a session that is already running.

The board is injected once at session start (decision 0003), so everything it learns
afterwards used to go nowhere. Claude Code binds a unix inbox socket per session and hands
its address to hooks before any hook runs, which makes a hook — not the model, and not the
founder — the thing that can close that gap.

The frame these tests pin is undocumented: it comes from the CLI's own `[uds-messaging]`
log line. That is exactly why it is asserted byte for byte here and proved again by
`claude-bp-doctor`. If a future release changes it, this goes red rather than the channel
going quietly dead.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from helpers import BIN, RepoCase, session_record_for, sid

from claude_bestpractice import inbox, sessions


class Listener:
    """A real AF_UNIX server. Nothing here is mocked — the wire is the thing under test.

    Bound under `/tmp` directly rather than the case's fixture directory: a unix socket
    path is capped near 108 bytes and Claude Code has its own `ENAMETOOLONG` failure for
    the same reason, so a test that nested it deeper would fail for the wrong cause.
    """

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.address = os.path.join(self.dir, "s.sock")
        self.received: list[bytes] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.address)
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            conn.settimeout(2)
            buffered = b""
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buffered += chunk
            except OSError as exc:
                # Kept rather than discarded: a short read that vanishes silently turns
                # "the frame never arrived" into "the frame was wrong", and the assertion
                # would then name the wrong defect.
                buffered += json.dumps({"type": "read-error", "detail": str(exc)}).encode()
            self.received.append(buffered)
            conn.close()

    def env(self, token: str = "TOK") -> dict:
        return {inbox.SOCKET_ENV: self.address, inbox.TOKEN_ENV: token}

    def frames(self) -> list[dict]:
        return [
            json.loads(line)
            for blob in self.received
            for line in blob.decode("utf-8").splitlines()
        ]

    def settle(self, expected: int, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while len(self.received) < expected and time.time() < deadline:
            time.sleep(0.02)

    def close(self) -> None:
        self.server.close()


class TestTheWire(unittest.TestCase):
    def test_the_frames_are_exactly_what_the_receiver_reads(self):
        """Pinned against the CLI's own log line, which is the only place it is written
        down. Newline-delimited JSON, auth first, then a user message."""
        raw = inbox.frames("TOK", "hello").decode("utf-8")
        self.assertEqual(
            raw,
            '{"type":"auth","token":"TOK"}\n'
            '{"type":"user","message":{"role":"user","content":"hello"}}\n',
        )

    def test_without_a_token_only_the_message_goes(self):
        lines = inbox.frames("", "hello").decode("utf-8").splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("user", json.loads(lines[0])["type"])

    def test_a_newline_in_the_text_cannot_break_the_framing(self):
        """The receiver splits on newlines, so a note containing one would arrive as two
        frames — the second of them unparseable — if JSON escaping were not doing its job."""
        raw = inbox.frames("", "first\nsecond").decode("utf-8")
        self.assertEqual(1, len(raw.splitlines()))
        self.assertEqual("first\nsecond", json.loads(raw)["message"]["content"])

    def test_nothing_larger_than_the_receivers_buffer_is_sent(self):
        """A line over a mebibyte drops the connection at the far end. Refusing here keeps
        that a fact about our own guard rather than about their error handling."""
        listener = Listener()
        self.addCleanup(listener.close)
        self.assertFalse(inbox._send(listener.address, "", "x" * (inbox.WIRE_LIMIT + 1)))
        self.assertEqual([], listener.received)

    def test_a_non_string_body_is_refused_before_the_socket(self):
        """A TRUTHY non-string, which is the only case the type check earns its place on:
        the receiver ignores a `user` frame whose content is not a string, so a number sent
        here would be serialised, accepted by the socket, and silently dropped."""
        listener = Listener()
        self.addCleanup(listener.close)
        self.assertFalse(inbox._send(listener.address, "", 12345))
        self.assertEqual([], listener.received)


class TestTheQueue(RepoCase):
    def test_a_fact_reaches_the_session_it_was_addressed_to(self):
        listener = Listener()
        self.addCleanup(listener.close)
        self.assertTrue(inbox.post(self.ctx(), "peer", "the suite is RED", sender="me"))
        self.assertEqual(1, inbox.drain(self.ctx(), "peer", env=listener.env()))
        listener.settle(1)
        body = listener.frames()[-1]["message"]["content"]
        self.assertIn("the suite is RED", body)
        self.assertTrue(body.startswith(inbox.PREFIX))

    def test_the_same_fact_is_not_queued_twice(self):
        """The condition that produces a note is usually still true on the next call that
        checks it, so without this the channel is a loop rather than a channel."""
        ctx = self.ctx()
        self.assertTrue(inbox.post(ctx, "peer", "you hold the lease on src/a.py"))
        self.assertFalse(inbox.post(ctx, "peer", "you  hold   the LEASE on src/a.py"))
        self.assertEqual(1, len(inbox.pending(ctx, "peer")))

    def test_a_delivered_fact_is_not_delivered_again(self):
        listener = Listener()
        self.addCleanup(listener.close)
        ctx = self.ctx()
        inbox.post(ctx, "peer", "the suite is RED")
        self.assertEqual(1, inbox.drain(ctx, "peer", env=listener.env()))
        self.assertEqual(0, inbox.drain(ctx, "peer", env=listener.env()))

    def test_a_fact_nobody_collected_in_time_is_retired_rather_than_delivered(self):
        """Arriving late is worse than not arriving: the lease was released, the branch was
        rebased, and the note reads as current."""
        listener = Listener()
        self.addCleanup(listener.close)
        ctx = self.ctx()
        inbox.post(ctx, "peer", "another session is blocked on src/a.py")
        path = inbox._path(ctx, "peer")
        notes = json.loads(path.read_text())
        notes[0]["created_at"] = time.time() - inbox.STALE_SECONDS - 1
        path.write_text(json.dumps(notes))

        self.assertEqual(0, inbox.drain(ctx, "peer", env=listener.env()))
        self.assertEqual([], listener.received)
        self.assertTrue(json.loads(path.read_text())[0]["stale"])

    def test_a_burst_does_not_become_a_wall_of_turns(self):
        listener = Listener()
        self.addCleanup(listener.close)
        ctx = self.ctx()
        for n in range(inbox.MAX_PER_DRAIN + 3):
            inbox.post(ctx, "peer", f"fact number {n}")
        self.assertEqual(inbox.MAX_PER_DRAIN, inbox.drain(ctx, "peer", env=listener.env()))

    def test_a_broadcast_skips_the_session_that_sent_it(self):
        ctx = self.ctx()
        for name in ("a", "b"):
            sessions.register(ctx, session_record_for(ctx, sid(self.repo, name)))
        told = inbox.broadcast(ctx, sid(self.repo, "a"), "main moved")
        self.assertEqual(1, told)
        self.assertEqual([], inbox.pending(ctx, sid(self.repo, "a")))
        self.assertEqual(1, len(inbox.pending(ctx, sid(self.repo, "b"))))


class TestItNeverCostsTheFounderATurn(RepoCase):
    """`drain` is called from a gate that fails CLOSED. Every failure here has to be
    contained, or a vanished socket becomes a refused tool call."""

    def test_a_socket_that_is_gone_is_not_an_error(self):
        ctx = self.ctx()
        inbox.post(ctx, "peer", "the suite is RED")
        gone = os.path.join(tempfile.mkdtemp(dir="/tmp"), "missing.sock")
        self.assertEqual(0, inbox.drain(ctx, "peer", env={inbox.SOCKET_ENV: gone}))

    def test_a_session_without_messaging_is_simply_silent(self):
        ctx = self.ctx()
        inbox.post(ctx, "peer", "the suite is RED")
        self.assertEqual(0, inbox.drain(ctx, "peer", env={}))
        self.assertFalse(inbox.deliverable({}))

    def test_one_malformed_entry_does_not_bury_the_note_beside_it(self):
        """A torn file is already handled a layer down, where `read_json` returns the
        default. What is NOT handled there is a well-formed array holding the wrong shape:
        one junk entry would abort the whole drain, and the real note behind it would never
        be delivered at all."""
        listener = Listener()
        self.addCleanup(listener.close)
        ctx = self.ctx()
        inbox.post(ctx, "peer", "the suite is RED")
        path = inbox._path(ctx, "peer")
        path.write_text(json.dumps([7, "junk", *json.loads(path.read_text())]))

        self.assertEqual(1, inbox.drain(ctx, "peer", env=listener.env()))
        listener.settle(1)
        self.assertIn("the suite is RED", listener.frames()[-1]["message"]["content"])


class TestAReindexDoesNotEatTheQueue(RepoCase):
    def test_an_undelivered_note_survives_a_purge(self):
        """Tier B is described as derived, and a queued note is not: the lease conflict
        that produced it happened at a moment no rescan can reconstruct. `claude-bp
        reindex` wiping it would be silent and permanent, which is the failure the carried
        list already exists to prevent."""
        from claude_bestpractice import store

        ctx = self.ctx()
        inbox.post(ctx, "peer", "another session is blocked on src/a.py")
        store.purge_tier_b(ctx)
        self.assertEqual(1, len(inbox.pending(ctx, "peer")))


class TestTheGateDelivers(RepoCase):
    def test_the_holder_of_a_lease_is_told_someone_is_blocked_on_it(self):
        """The refused session learns from the denial. The holder is the only one who can
        end the wait, and learns nothing unless it is told."""
        ctx = self.ctx()
        holder = sid(self.repo, "holder")
        blocked = sid(self.repo, "blocked")
        for who in (holder, blocked):
            sessions.register(ctx, session_record_for(ctx, who))
        self.assertIsNone(sessions.acquire_lease(ctx, holder, "src/a.py"))

        # RAW on the wire, composed in the registry: a gate composes (harness id,
        # worktree) itself, so handing it an already-composed id makes it a third session.
        result = self.run_hook(
            "pre-tool",
            {
                "session_id": "blocked",
                "tool_name": "Write",
                "tool_input": {"file_path": "src/a.py", "content": "x = 1\n"},
            },
        )
        # Named explicitly so an earlier gate refusing for its own reason fails this test
        # instead of passing it: the drift and staleness gates both sit above this one.
        self.assertIn("is editing src/a.py right now", result.stdout)
        queued = inbox.pending(ctx, holder)
        self.assertEqual(1, len(queued))
        self.assertIn("blocked on src/a.py", queued[0]["text"])

    def test_the_gate_delivers_what_is_waiting_before_the_call_it_is_about(self):
        listener = Listener()
        self.addCleanup(listener.close)
        ctx = self.ctx()
        me = sid(self.repo, "me")
        sessions.register(ctx, session_record_for(ctx, me))
        inbox.post(ctx, me, "main moved under you")

        self.run_hook(
            "pre-tool",
            {"session_id": "me", "tool_name": "Write",
             "tool_input": {"file_path": "src/b.py", "content": "y = 2\n"}},
            env={**os.environ, **listener.env()},
        )
        listener.settle(1)
        self.assertIn(
            "main moved under you",
            " ".join(f["message"]["content"] for f in listener.frames() if f["type"] == "user"),
        )


if __name__ == "__main__":
    unittest.main()


class TestAQuestionIsAnObligation(RepoCase):
    """A fact tells; a question expects an answer, and the difference has to be structural
    or it is a fact with a question mark.

    Telling the lease holder left them free to say nothing and keep the file: the blocked
    session waited out the full thirty-minute TTL while the holder had committed twenty
    minutes earlier and moved on. Reported from a live repository (#166).
    """

    def ask(self, to: str = "them", frm: str = "me", text: str = "are you still in schemas.py?"):
        from claude_bestpractice import inbox

        return inbox.ask(self.ctx(), to, text, sender=frm)

    def open_for(self, who: str = "them"):
        from claude_bestpractice import inbox

        return inbox.open_asks(self.ctx(), who)

    def test_an_ask_stays_open_until_it_is_answered(self):
        self.ask()
        self.assertEqual(1, len(self.open_for()))

    def test_answering_closes_it(self):
        from claude_bestpractice import inbox

        got = self.ask()
        self.assertTrue(got, "the ask was not queued at all")
        self.assertTrue(inbox.answer(self.ctx(), "them", got, "committed, take it"))
        self.assertEqual([], self.open_for())

    def test_the_answer_reaches_the_one_who_asked(self):
        from claude_bestpractice import inbox

        got = self.ask()
        inbox.answer(self.ctx(), "them", got, "committed, take it")
        said = [n["text"] for n in inbox.pending(self.ctx(), "me")]
        self.assertTrue(any("committed, take it" in t for t in said), said)

    def test_answering_something_nobody_asked_changes_nothing(self):
        from claude_bestpractice import inbox

        self.ask()
        self.assertFalse(inbox.answer(self.ctx(), "them", "deadbeef", "sure"))
        self.assertEqual(1, len(self.open_for()))

    def test_an_empty_answer_is_not_an_answer(self):
        from claude_bestpractice import inbox

        got = self.ask()
        self.assertFalse(inbox.answer(self.ctx(), "them", got, "   "))
        self.assertEqual(1, len(self.open_for()))

    def test_a_fact_is_not_an_ask(self):
        """`post` must not start holding turns — most of what this channel carries is a
        fact nobody has to reply to."""
        from claude_bestpractice import inbox

        inbox.post(self.ctx(), "them", "the suite is RED on main", sender="me")
        self.assertEqual([], self.open_for())


class TestOurOwnFactIsNotTheRecipientsTask(RepoCase):
    """A fact arrives as a user turn, and the prompt reader reads user turns.

    `[claude-bestpractice] another session is blocked on store.py…` is long and it names a
    path, so it cleared every test for a statement of work: the note became the recipient's
    task, was quoted back by every drift refusal, named their branch, and added ITS paths to
    their allowed scope. The same defect as #106, #118 and #166 — through the one door this
    plugin built for itself, because its two voice markers had drifted apart by one
    character: `[claude-bestpractice]` against `claude-bestpractice:`.
    """

    def deliver(self, text: str):
        return subprocess.run(
            [sys.executable, str(BIN / "prompt-capture")],
            input=json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                              "prompt": text, "cwd": str(self.repo)}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )

    def record(self):
        from claude_bestpractice import sessions

        return sessions.get(self.ctx(), sid(self.repo, "s1"))

    def test_a_delivered_fact_never_becomes_the_task(self):
        from claude_bestpractice import inbox

        self.write("store.py", "x = 1\n")
        self.deliver("перепиши store.py так, чтобы запись была атомарной")
        self.deliver(f"{inbox.PREFIX} another session is blocked on store.py, which you "
                     "hold. Are you still in it, or can they take it?")

        self.assertEqual("перепиши store.py так, чтобы запись была атомарной",
                         self.record().task_statement)

    def test_it_is_not_the_task_of_a_session_that_has_none_either(self):
        """The blank-board fallback keeps «Делай» over nothing. It must not keep ours."""
        from claude_bestpractice import inbox

        self.deliver(f"{inbox.PREFIX} the suite is RED on main, and you are branched off it")
        self.assertEqual("", self.record().task_statement)

    def test_the_channel_says_what_it_carried(self):
        from claude_bestpractice import inbox

        ctx = self.ctx()
        inbox.post(ctx, "them", "the suite is RED on main", sender="me")
        inbox.ask(ctx, "them", "are you still in store.py?", sender="me")
        moved = inbox.carried(ctx)

        self.assertEqual(2, moved["queued"])
        self.assertEqual(0, moved["delivered"])
        self.assertEqual(1, moved["asks"])
        self.assertEqual(0, moved["answered"])

        inbox.answer(ctx, "them", inbox.open_asks(ctx, "them")[0]["key"][:12], "yes, still in it")
        self.assertEqual(1, inbox.carried(ctx)["answered"])

        # Delivered, over a real socket, because "queued" and "arrived" are the two
        # numbers the keep-or-cut question actually turns on.
        listener = Listener()
        self.addCleanup(listener.close)
        inbox.drain(ctx, "them", env=listener.env())
        self.assertEqual(2, inbox.carried(ctx)["delivered"])
