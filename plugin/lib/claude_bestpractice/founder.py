"""The founder's own word: heard as they send it, and read back from where the harness wrote it.

`prompt-capture` hears a message when it starts a turn. One sent while the session is working,
typed in the terminal or sent from the phone, is folded into the running turn at the next tool
result, and no hook fires for it. The founder sent `+merge` that way three times in a row; it
was never recorded, and every merge after it was refused as if they had said nothing (#251).

The harness writes every message it delivers into the session's transcript, and marks who it
came from. So a gate that is about to refuse for want of the founder's word reads the
transcript first (`catch_up`), and records what it finds exactly as `prompt-capture` records
what it hears (`heard`): one road for the word, whichever door it came in by.

Only what a person sent counts: a `user` entry or a queued message the harness marks as a
human's, never a sidechain, a meta entry, a task notification or another agent's report. Of
that, only what they typed themselves (`founders_word`), never what they pasted or quoted. And
the transcript is theirs alone to write: `pre-tool` refuses a session's write to it.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, inbox, store
from .gitctx import GitContext

# Every word heard, from either door: its identity, when it was said, whose chat, what it
# allowed. And one baseline row, the moment this log began.
WORDS = "founder-words.jsonl"
_LOCK = "founder-words.lock"

# The same word, heard by `prompt-capture` and read back from the transcript, when the two
# cannot be matched by the prompt id: the hook runs as the message is submitted, and the
# harness stamps its entry within a second or two of that.
_SAME_WORD_SECONDS = 30.0

# What of a transcript is read: the end of it, where a word that is still owed lives. A long
# session's transcript runs to tens of megabytes, and this runs on a refusal path.
_MOST_BYTES = 64 * 1024 * 1024

# A line worth decoding at all. Nearly none are, and this keeps a large transcript cheap.
_A_LITERAL = re.compile(rb"(?i)\+(?:merge|release|deploy|migration)")


# ---------------------------------------------------------------------------------------------
# What the founder typed themselves, out of what reached the session as their message.

# Blocks the harness wraps around things the founder did not type. Named, because a founder
# pasting XML is asking for it to be read; `outside_markup` below is the part that does not
# depend on having guessed every name the harness will ever use.
ENVELOPES = (
    "ide_opened_file", "ide_selection", "ide_diagnostics", "system-reminder",
    "background-task", "command-name", "command-message", "local-command-stdout",
    "task-notification", "github-webhook-activity", "untrusted_external_data",
    "command-args", "local-command-caveat",
)

# Elements by any name, the way the harness writes them — including the pasted-content
# marker, whose closing tag carries the same `id="…"` its opening one does.
_OPENING_TAG = re.compile(r"<([A-Za-z][\w:.-]*)\b[^>]*>")
_CLOSING_TAG = re.compile(r"</([A-Za-z][\w:.-]*)\b[^>]*>")

# Claude Code wraps a collapsed paste in these when it marks pasted text for the model, and
# a paste is not something the founder typed. With the harness's own blocks, the names whose
# opener takes the rest of the message along when nothing closes it.
_UNCLOSED_TAKES_THE_REST = {name.lower() for name in (*ENVELOPES, "pasted_content")}

# The name every message of this plugin opens with, colon or not.
_OUR_NAME = "claude-bestpractice"

# A fenced block, opened and closed by the same run of backticks or tildes.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

# A unified-diff hunk header, which says how many lines of each side follow it.
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def outside_markup(text: str) -> str:
    """The text with every element taken out, each one to the LAST tag that could close it.

    The widest reading, on purpose. The harness puts whatever a background task printed
    inside `<task-notification>`, so a result that printed `</task-notification>` and then
    `+merge` closed the block early for any reader that stops at the first closing tag —
    and the line after it was recorded as the founder's acceptance. What the harness wraps
    is never the founder's typing however it nests, so nothing inside the outermost reading
    of an element counts. Linear: every closing tag is found once.
    """
    last_close = {found.group(1).lower(): found for found in _CLOSING_TAG.finditer(text)}
    kept: list[str] = []
    cursor = 0
    for opening in _OPENING_TAG.finditer(text):
        if opening.start() < cursor:
            continue
        name = opening.group(1).lower()
        closing = last_close.get(name)
        if closing is not None and closing.start() > opening.start():
            end = closing.end()
        elif name in _UNCLOSED_TAKES_THE_REST:
            end = len(text)
        else:
            continue
        kept.append(text[cursor:opening.start()])
        cursor = end
    kept.append(text[cursor:])
    return "".join(kept)


def _outside_fences(text: str) -> str:
    """The lines outside fenced code blocks. A fence nobody closed runs to the end.

    Split on the newline alone, here and in the two readers below, so what counts as a line
    for them is what counts as one for the literal itself: a `\\r` or a U+2028 before `+merge`
    has never made it a line of its own.
    """
    kept: list[str] = []
    fence = ""
    for line in text.split("\n"):
        found = _FENCE.match(line)
        if fence:
            if found and found.group(1)[0] == fence[0] and len(found.group(1)) >= len(fence):
                fence = ""
        elif found:
            fence = found.group(1)
        else:
            kept.append(line)
    return "\n".join(kept)


def _outside_hunks(text: str) -> str:
    """The lines outside unified-diff hunks, each hunk as long as its header says.

    An added line of a diff is `+` and its text, so a pasted diff of a file holding the line
    `merge` carried a `+merge` line. The header's counts end the hunk; a line no diff could
    hold ends it sooner, because a paste gets cut.
    """
    kept: list[str] = []
    left = (0, 0)
    for line in text.split("\n"):
        left, inside = _through_a_hunk(line, left)
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def _through_a_hunk(line: str, left: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    """How many lines of each side a hunk has left after `line`, and whether `line` is its."""
    old, new = left
    bare = line.rstrip("\r")
    if (old > 0 or new > 0) and (not bare or bare[0] in " +-\\"):
        mark = bare[:1] or " "
        return (old - (mark in " -"), new - (mark in " +")), True
    header = _HUNK.match(line)
    if header:
        return (int(header.group(1) or 1), int(header.group(2) or 1)), True
    return (0, 0), False


def _what_follows_our_voice(text: str) -> str:
    """After a message of this plugin's, only the lines of the founder's word that END it.

    Every message this plugin writes ends in words of its own, never in something it quoted,
    and its middle can quote a path, a command or the task statement — any of which a session
    can put a `+merge` line into. So the run of literal lines closing the message is the
    founder's, added below what they pasted, and everything above it is taken as ours.
    Dropping the whole message instead ignored a founder who pasted a refusal and wrote
    `+merge` under it, and told them nothing.
    """
    closing: list[str] = []
    for line in reversed(text.rstrip().split("\n")):
        if line.strip() and not config.approvals_in(line):
            break
        closing.append(line)
    return "\n".join(reversed(closing))


def founders_word(prompt: str) -> str:
    """The part of a message a grant may be read from: what the founder typed themselves.

    Not what the harness wrapped around it, not a fenced block or a diff they pasted, and
    not this plugin's own voice — each of those carries lines nobody meant as consent, and
    the grant is the one thing the gated session must never be able to write (decision
    0010). Read from the message as it arrived, before any envelope was stripped: stripping
    first is what let a block's early closing tag cut it in two.
    """
    typed = _outside_hunks(_outside_fences(outside_markup(prompt)))
    if typed.strip().startswith((_OUR_NAME, *inbox.MARKERS)):
        return _what_follows_our_voice(typed)
    return typed


# ---------------------------------------------------------------------------------------------
# The words a transcript holds.


@dataclass(frozen=True)
class Word:
    """One message of the founder's that allowed something, as the harness recorded it."""

    ident: str
    at: float
    approvals: dict[str, str]


def said(transcript_path: str) -> list[Word]:
    """The founder's words in this transcript, oldest first. Empty when it cannot be read.

    The transcript format is internal to Claude Code and changes between releases, so every
    failure here reads as "nothing said" rather than raising: the gate then refuses as it did
    before, which is the direction a reader of an unknown format is allowed to be wrong in.
    """
    path = Path(transcript_path) if transcript_path else None
    if path is None:
        return []
    try:
        stamp = path.stat()
    except OSError:
        return []
    key = (str(path), stamp.st_size, stamp.st_mtime_ns)
    if key not in _READ:
        _READ.clear()
        _READ[key] = _read(path, stamp.st_size)
    return _READ[key]


# One reading per transcript state, for the length of a hook: a gate that waits for the word
# asks for it several times a second.
_READ: dict[tuple[str, int, int], list[Word]] = {}


def _read(path: Path, size: int) -> list[Word]:
    found: list[Word] = []
    try:
        with path.open("rb") as handle:
            if size > _MOST_BYTES:
                handle.seek(size - _MOST_BYTES)
                handle.readline()
            for raw in handle:
                word = _word_in(raw) if _A_LITERAL.search(raw) else None
                if word is not None:
                    found.append(word)
    except OSError:
        return []
    return found


def _word_in(raw: bytes) -> Word | None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    message = _from_a_person(entry) if isinstance(entry, dict) else None
    if message is None:
        return None
    ident, text, at = message
    approvals = config.approvals_in(founders_word(text))
    return Word(ident, at, approvals) if approvals else None


def _from_a_person(entry: dict[str, Any]) -> tuple[str, str, float] | None:
    """(identity, text, when) of a message a person sent, or None for anything else.

    A turn the founder started is a `user` entry; one they sent while the session worked is a
    `queued_command` attachment folded into the running turn. Everything else the harness
    files under those types — tool results, sidechains, meta entries, task notifications,
    another agent's report — is not them.
    """
    if entry.get("isSidechain") or entry.get("isMeta"):
        return None
    found = _as_a_turn(entry) if entry.get("type") == "user" else _as_queued(entry)
    if found is None:
        return None
    source, content, ident = found
    text = _text_of(content)
    at = _moment(source.get("timestamp") or entry.get("timestamp"))
    if not text or not at or not _a_persons(source):
        return None
    return str(ident or f"at:{at:.3f}"), text, at


def _as_a_turn(entry: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    """A `user` entry as (what describes it, its content, its identity)."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return entry, content, entry.get("promptId") or entry.get("uuid")


def _as_queued(entry: dict[str, Any]) -> tuple[dict[str, Any], Any, Any] | None:
    """A queued message folded into a turn, as `_as_a_turn` reads a turn; None for the rest."""
    source = entry.get("attachment")
    if entry.get("type") != "attachment" or not isinstance(source, dict):
        return None
    if source.get("type") != "queued_command" or source.get("isMeta"):
        return None
    if source.get("commandMode", "prompt") != "prompt":
        return None
    return source, source.get("prompt"), source.get("source_uuid") or entry.get("uuid")


def _a_persons(source: dict[str, Any]) -> bool:
    """Whether the harness says a person sent this. Where it says nothing, the text decides.

    `origin` names the sender: `human`, or a task notification, a peer agent, the system. An
    entry written before the harness said so is judged as `prompt-capture` judges every
    message it is handed — by what it says — with the harness's own records set aside.
    """
    origin = source.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    return source.get("promptSource") != "system"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _moment(stamp: Any) -> float:
    """An ISO timestamp as the harness writes it, in seconds. 0.0 when it is not one."""
    if not isinstance(stamp, str) or not stamp:
        return 0.0
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------------------------
# One road for the word.


def heard(ctx: GitContext, approvals: dict[str, str], session_id: str, *, at: float,
          ident: str = "", source: str = "prompt") -> None:
    """Record the founder's word, said at `at` in the chat `session_id` belongs to.

    A `+merge` names the pull requests that chat had open at that moment (decision 0023);
    naming them reads the session registry, and a word that cannot be pooled is still the
    word: recorded as the one merge it always was, never lost with the pool.
    """
    if not approvals:
        return
    from . import hookio, pullrequest

    try:
        granted = pullrequest.accept_merges(ctx, approvals, session_id, at=at)
    except Exception:  # noqa: BLE001 - the word outlives its pool
        granted = dict(approvals)
    config.record_switches(ctx, granted)
    _log(ctx)
    store.append_jsonl(store.tier_b(ctx, WORDS), {
        "id": ident, "at": at, "source": source, "kinds": sorted(approvals),
        "harness": hookio.harness_of(session_id, str(ctx.worktree_root)),
    })


def catch_up(ctx: GitContext, session_id: str, transcript_path: str) -> None:
    """Record every word of the founder's in this chat's transcript that nothing recorded yet.

    Never one said before this log began: those were the previous version's to hear, and
    whatever they allowed is either on record already or was spent long ago. Never raises —
    it runs where a gate is about to refuse, and a failure here leaves the refusal standing.
    """
    words = said(transcript_path)
    if not words:
        return
    try:
        with store.file_lock(store.tier_b(ctx, _LOCK)):
            _catch_up(ctx, session_id, words)
    except Exception:  # noqa: BLE001 - see above
        return


def _catch_up(ctx: GitContext, session_id: str, words: list[Word]) -> None:
    from . import hookio

    rows = _log(ctx)
    since = max((float(row.get("baseline") or 0) for row in rows), default=0.0)
    harness = hookio.harness_of(session_id, str(ctx.worktree_root))
    for word in words:
        if word.at < since or _already(rows, word, harness):
            continue
        heard(ctx, word.approvals, session_id, at=word.at, ident=word.ident, source="transcript")
        rows.append({"id": word.ident, "at": word.at, "harness": harness,
                     "kinds": sorted(word.approvals)})


def _already(rows: list[dict[str, Any]], word: Word, harness: str) -> bool:
    """Was this word recorded already, by its id or as the same word heard by the hook?"""
    kinds = sorted(word.approvals)
    for row in rows:
        if row.get("id") and row.get("id") == word.ident:
            return True
        if (row.get("harness") == harness and row.get("kinds") == kinds
                and abs(float(row.get("at") or 0) - word.at) <= _SAME_WORD_SECONDS):
            return True
    return False


def begin(ctx: GitContext) -> None:
    """Begin the log of words heard, where it never was: at the upgrade that brings it.

    Words said before it were the previous version's to hear. Whatever they allowed is on
    record already or was spent long ago, and read back again one of them would allow a
    merge nobody asked for. Begun on the first session start after the upgrade, so that a
    word said in any session after it is read back; a session already running when the
    upgrade landed begins it the first time it needs it.
    """
    _log(ctx)


def _log(ctx: GitContext) -> list[dict[str, Any]]:
    """The log of words heard, begun now if it never was."""
    path = store.tier_b(ctx, WORDS)
    rows = [row for row in store.read_jsonl(path) if isinstance(row, dict)]
    if not any(row.get("baseline") for row in rows):
        begun = {"baseline": time.time()}
        store.append_jsonl(path, begun)
        rows.append(begun)
    return rows


def writes_the_record(target: Path, transcript_path: str) -> bool:
    """Would writing `target` change what the founder's word is read back from?

    A session that could append a line to a transcript could write the founder's word into
    it, and that word is the one thing the gated session must never be able to write
    (decision 0010). The harness keeps a project's transcripts in one directory, a child's
    and a subagent's beside the session's own; the notes it also keeps there are left alone.
    """
    if not transcript_path:
        return False
    try:
        home = Path(transcript_path).parent.resolve()
        target = target.resolve()
    except (OSError, RuntimeError):
        return False
    if target == home or target in home.parents:
        return True
    return home in target.parents and target.suffix == ".jsonl"


def awaited(ctx: GitContext, key: str, session_id: str, transcript_path: str) -> bool:
    """`config.awaited`, with the chat's transcript read before the answer is no."""
    def check() -> bool:
        if config.approved(ctx, key):
            return True
        catch_up(ctx, session_id, transcript_path)
        return config.approved(ctx, key)

    return bool(config.within_grace(check))
