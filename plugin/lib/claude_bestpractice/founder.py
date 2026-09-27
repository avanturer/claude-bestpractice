"""What the founder typed themselves, out of what reached the session as their message.

A message arrives carrying whatever the harness wrapped around it, whatever the founder pasted
into it, and sometimes this plugin's own voice, and a grant must be read from none of those.
This is that reader, kept where every gate that needs the founder's word can use it rather
than inside the one hook that used to hold it.
"""

from __future__ import annotations

import re

from . import config, inbox

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
