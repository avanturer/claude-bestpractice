"""Deliver a fact into a session that is already running.

The board is injected once, at session start — decision 0003, because injecting it every
turn costs O(T^2) against O(T). That bought a cheap board and left one hole: a session
already running could not be told anything new. A lease taken on the file it is editing, a
baseline that moved under it, a suite that went red on its path — all of it waited for a
restart a long session never performs.

Claude Code binds a unix socket per session for exactly this, and exports its path and
per-session token to hooks BEFORE any hook runs. So the fact can be delivered by a hook,
without the model's cooperation and without a prompt to the founder.

The route is deliberately indirect. A session writes a note addressed to a peer into Tier
B; the PEER'S OWN hook delivers it into its OWN socket with its own token, at its next tool
call. Nothing here handles another session's credentials, the message arrives in the class
Claude Code calls `child` rather than `peer` so no inbound dialog can hold it, and "at its
next tool call" is precisely the moment before that session acts on what it does not know.

The frame below is not in the documentation. Claude Code prints it in its own
`[uds-messaging]` log line, and the receiver's checks were read off the binary: a
connection that does not authenticate is closed, a frame without a valid `type` is
ignored, a `user` frame whose content is not a string is ignored, and a line longer than a
mebibyte drops the connection. Being undocumented is why `claude-bp-doctor` proves this
channel by using it rather than by asserting it — if the frame ever changes, a gate goes
red instead of a feature going quietly dead.

Fails open, everywhere. A fact that did not arrive is a worse board; a hook that raised is
a worse plugin.
"""

from __future__ import annotations

import json
import os
import socket
import time

from . import sessions, store
from .gitctx import GitContext

DIRNAME = "inbox"

# Exported to hooks before any hook runs, including SessionStart. Each session exports its
# OWN socket, never one inherited from a parent — which is what makes the indirect route
# above correct by construction rather than by care.
SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"
TOKEN_ENV = "CLAUDE_CODE_MESSAGING_TOKEN"

# Every delivered note costs the recipient exactly what a typed prompt costs. That is the
# whole reason this is four facts and not a feed.
MAX_CHARS = 600

# Undelivered for this long and the fact has moved on: the lease was released, the branch
# was rebased. Arriving late is worse than not arriving, because it reads as current.
STALE_SECONDS = 30 * 60

# The same fact, to the same session, is not worth repeating inside a working day.
COOLDOWN_SECONDS = 6 * 3600

# A burst must not turn into a wall of user turns. Anything held back is not lost; the
# next tool call is moments away.
MAX_PER_DRAIN = 2

RETENTION_SECONDS = 24 * 3600
MAX_NOTES = 64

# The receiver drops a connection buffering more than a mebibyte without a newline. We are
# three orders of magnitude below that; the guard is here so that stays true by test and
# not by assumption.
WIRE_LIMIT = 1 << 20

CONNECT_TIMEOUT = 2.0

# Names the sender in the recipient's transcript. Without it the founder reads a line that
# looks like something they typed and does not remember typing.
PREFIX = "[claude-bestpractice]"


def deliverable(env: dict | None = None) -> bool:
    """Whether this session can be delivered to at all.

    False on Windows, where there is no `AF_UNIX` and Claude Code offers no messaging, and
    false before the feature flag that binds the inbox has arrived.
    """
    env = os.environ if env is None else env
    return bool(hasattr(socket, "AF_UNIX") and env.get(SOCKET_ENV))


def _path(ctx: GitContext, session_id: str):
    return store.tier_b(ctx, DIRNAME, f"{sessions.safe_id(session_id)}.json")


def _notes(box_value: object) -> list[dict]:
    if not isinstance(box_value, list):
        return []
    return [n for n in box_value if isinstance(n, dict)]


def _pruned(notes: list[dict], now: float) -> list[dict]:
    kept = [n for n in notes if now - float(n.get("created_at") or 0) < RETENTION_SECONDS]
    return kept[-MAX_NOTES:]


def post(ctx: GitContext, recipient: str, text: str, sender: str = "") -> bool:
    """Queue a fact for another session. False when it already knows it.

    Deduplicated on the claim itself, so the same fact re-derived on every tool call
    delivers once. Without this the channel would be a loop: the condition that produces
    the note is usually still true on the next call that checks it.

    Never raises, for the same reason `drain` never does — every caller is a gate whose
    failure mode is refusing the founder's work.
    """
    try:
        return _queue(ctx, recipient, text, sender)
    except Exception:  # noqa: BLE001 - a fact that failed to queue must not fail a gate
        return False


def broadcast(ctx: GitContext, sender: str, text: str) -> int:
    """Tell every other live session one thing. Returns how many were told."""
    told = 0
    for other in sessions.live_sessions(ctx, exclude=sender):
        if post(ctx, other.session_id, text, sender=sender):
            told += 1
    return told


def _queue(ctx: GitContext, recipient: str, text: str, sender: str, asks: bool = False) -> bool:
    from . import board

    text = " ".join(str(text).split())[:MAX_CHARS]
    if not text or not recipient:
        return False
    key = board.item_key(text, recipient, [])
    now = time.time()
    with store.guarded_json(_path(ctx, recipient), default=[]) as box:
        notes = _notes(box[0])
        for note in notes:
            if note.get("key") != key:
                continue
            delivered = note.get("delivered_at")
            if delivered is None or now - float(delivered) < COOLDOWN_SECONDS:
                return False
        notes.append({
            "key": key,
            "text": text,
            "from": sender,
            "created_at": now,
            "delivered_at": None,
            "asks": asks,
            "answered_at": None,
        })
        box[0] = _pruned(notes, now)
    return True


def ask(ctx: GitContext, recipient: str, question: str, sender: str) -> str:
    """Put a QUESTION to another session. Returns its id, or "" when it was not queued.

    A fact tells; an ask expects an answer, and the difference has to be structural or it
    is a fact with a question mark. The recipient's Stop gate refuses to end a turn while
    one is unanswered — the same shape as an open pull request and an unlisted change,
    because it is the same problem: a thing somebody is waiting on that the session can
    otherwise walk past.

    Cheap to hold and bounded by the escalation ceiling every gate here shares, so a
    sibling that never answers stops that session for four turns rather than forever.
    """
    if not _queue(ctx, recipient, question, sender, asks=True):
        return ""
    for note in _notes(store.read_json(_path(ctx, recipient), default=[])):
        if note.get("asks") and note.get("from") == sender and not note.get("answered_at"):
            return str(note.get("key") or "")[:12]
    return ""


def open_asks(ctx: GitContext, session_id: str) -> list[dict]:
    """Questions put to this session that it has not answered."""
    notes = _notes(store.read_json(_path(ctx, session_id), default=[]))
    return [n for n in notes if n.get("asks") and not n.get("answered_at")]


def answer(ctx: GitContext, session_id: str, ask_id: str, text: str) -> bool:
    """Answer a question, and send the answer back. False when no such question is open.

    Answering is what closes it. Two backstops keep that from wedging anybody and both
    are deliberate: the escalation ceiling every gate here shares releases the turn after
    four refusals, and retention drops the note after a day. Neither is a way to win by
    waiting — four blocked turns is expensive enough to answer instead.
    """
    said = " ".join(str(text).split())[:MAX_CHARS]
    if not said:
        return False
    asker = ""
    with store.guarded_json(_path(ctx, session_id), default=[]) as box:
        notes = _notes(box[0])
        for note in notes:
            if str(note.get("key") or "")[:12] != ask_id or note.get("answered_at"):
                continue
            note["answered_at"] = time.time()
            asker = str(note.get("from") or "")
            break
        box[0] = notes
    if not asker:
        return False
    post(ctx, asker, f"answer from {session_id[:8]}: {said}", sender=session_id)
    return True


def pending(ctx: GitContext, session_id: str) -> list[dict]:
    """What is queued for a session and has not been delivered yet."""
    notes = _notes(store.read_json(_path(ctx, session_id), default=[]))
    return [n for n in notes if n.get("delivered_at") is None]


def carried(ctx: GitContext) -> dict[str, int]:
    """What this channel has actually moved, across every session on this clone.

    Every other mechanism in this plugin is justified by a measured failure — twenty
    minutes waiting on a lease, seventy seconds of suite becoming twenty, thirteen trees on
    one database. This one has never had a number against it, and "is it worth keeping"
    cannot be answered by taste. Counted from what the notes already record; nothing new is
    written to make this possible.
    """
    out = {"queued": 0, "delivered": 0, "asks": 0, "answered": 0}
    try:
        boxes = sorted(store.tier_b(ctx, DIRNAME).glob("*.json"))
    except OSError:
        return out
    for box in boxes:
        for note in _notes(store.read_json(box, default=[])):
            out["queued"] += 1
            out["delivered"] += note.get("delivered_at") is not None
            out["asks"] += bool(note.get("asks"))
            out["answered"] += bool(note.get("answered_at"))
    return out


def frames(token: str, text: str) -> bytes:
    """The wire: newline-delimited JSON, auth first when there is a token.

    Separated from the socket so the format can be asserted byte for byte in a test
    without one, and so the size guard is provable.
    """
    lines = []
    if token:
        lines.append({"type": "auth", "token": token})
    lines.append({"type": "user", "message": {"role": "user", "content": text}})
    payload = "".join(
        json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n" for line in lines
    )
    return payload.encode("utf-8")


def _send(address: str, token: str, text: str) -> bool:
    if not isinstance(text, str) or not text:
        return False
    encoded = frames(token, text)
    if len(encoded) > WIRE_LIMIT:
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect(address)
        sock.sendall(encoded)
    return True


def _deliver(notes: list[dict], address: str, token: str, now: float) -> int:
    sent = 0
    for note in notes:
        if sent >= MAX_PER_DRAIN:
            break
        if note.get("delivered_at") is not None:
            continue
        if now - float(note.get("created_at") or now) > STALE_SECONDS:
            # Retired, not delivered. Marking it keeps the queue from re-examining a note
            # that can never be sent, and `stale` is what makes that visible afterwards.
            note["delivered_at"] = now
            note["stale"] = True
            continue
        if _send(address, token, f"{PREFIX} {note.get('text', '')}"):
            note["delivered_at"] = now
            sent += 1
    return sent


def drain(ctx: GitContext, session_id: str, env: dict | None = None) -> int:
    """Deliver what this session was sent, into its own inbox. Never raises.

    Called from a gate that fails CLOSED, so every failure here has to be contained: a
    socket that vanished, a peer that never bound one, a torn queue file. None of those are
    reasons to refuse the founder's tool call.
    """
    env = os.environ if env is None else env
    if not deliverable(env):
        return 0
    path = _path(ctx, session_id)
    if not path.exists():
        return 0
    address = str(env.get(SOCKET_ENV) or "")
    token = str(env.get(TOKEN_ENV) or "")
    try:
        with store.guarded_json(path, default=[]) as box:
            notes = _notes(box[0])
            now = time.time()
            sent = _deliver(notes, address, token, now)
            box[0] = _pruned(notes, now)
        return sent
    except Exception:  # noqa: BLE001 - delivery is a courtesy; the gate around it is not
        return 0
