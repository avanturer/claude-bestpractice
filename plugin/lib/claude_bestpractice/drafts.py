"""Auto-draft decision records from what was already said.

Roughly half of repositories that adopt decision records stop under five entries, and
the ones that stop are the ones where writing a record started with a blank page. So
nothing here asks the founder to write anything. A Stop hook scans the turn's user
messages for correction markers — the moments where a human overruled the agent — and
appends a pre-filled draft to an inbox. Accepting is one command.

Correction markers are the signal because a correction is, by definition, information
the agent did not have and could not derive. "Use Postgres" is a preference. "No, not
Postgres, we already tried that and the ops burden killed us" is a decision record with
its Rejected section already written.

Extraction is textual and cheap. No model is called: a summariser here would cost
tokens on every turn to produce something the founder must read anyway, and it would
paraphrase away the exact wording that makes the record worth keeping.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import store
from .gitctx import GitContext

INBOX_FILE = "decision-inbox.jsonl"
MAX_DRAFT_CHARS = 400
MAX_DRAFTS_PER_TURN = 2

# Ordered by strength. A turn matching a strong marker is far more likely to carry a
# durable decision than one matching a weak one, and only the strongest match is kept.
_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("decision", re.compile(r"(?i)\b(?:we (?:decided|agreed|settled on)|let'?s go with|going with)\b")),
    ("rejection", re.compile(r"(?i)\b(?:don'?t|do not|never|stop) (?:use|using|do|doing|add|adding)\b")),
    # The word boundary lives inside each branch. A trailing \b after a comma never
    # matches — comma to space is not a boundary — which silently kills the branch.
    (
        "correction",
        re.compile(r"(?i)(?:^|[\s(])(?:no,|actually,|not that\b|instead of\b|rather than\b)"),
    ),
    ("constraint", re.compile(r"(?i)\b(?:must (?:always|never)|cannot|has to be|required to)\b")),
    ("rationale", re.compile(r"(?i)\bbecause\b.{0,120}\b(?:cost|slow|break|broke|fail|risk|ops|team|legal)\b")),
]

# Turns that look like corrections but carry no durable decision. Filtering these is
# what keeps the inbox worth opening.
_NOISE = re.compile(
    r"(?i)^(?:no,? (?:thanks|thank you|worries|problem)|not (?:now|yet|today)|"
    r"actually,? never ?mind|stop\.?$|wait\.?$)"
)


@dataclass
class Draft:
    marker: str
    quote: str
    branch: str
    session_id: str
    created_at: float
    subject_paths: list[str]

    def to_dict(self) -> dict:
        return {
            "marker": self.marker,
            "quote": self.quote,
            "branch": self.branch,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "subject_paths": self.subject_paths,
        }


def user_turns(transcript_path: str) -> list[str]:
    """Recent user messages, best effort.

    The transcript format is documented as internal and changing between releases, so
    every failure here degrades to an empty list rather than raising. Drafting is a
    convenience; breaking a session over it would be absurd.
    """
    if not transcript_path:
        return []
    try:
        text = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    turns: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "user" or record.get("isSidechain"):
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            body = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            continue
        body = body.strip()
        if body:
            turns.append(body)
    return turns


def classify(turn: str) -> str | None:
    """The strongest marker this turn matches, or None."""
    if len(turn) < 25 or _NOISE.match(turn.strip()):
        return None
    for name, pattern in _MARKERS:
        if pattern.search(turn):
            return name
    return None


def extract(turns: list[str], branch: str, session_id: str, subject_paths: list[str]) -> list[Draft]:
    now = time.time()
    drafts: list[Draft] = []
    seen: set[str] = set()

    for turn in reversed(turns):  # most recent first: a later correction supersedes
        marker = classify(turn)
        if not marker:
            continue
        quote = " ".join(turn.split())[:MAX_DRAFT_CHARS]
        key = quote[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        drafts.append(Draft(marker, quote, branch, session_id, now, list(subject_paths[:8])))
        if len(drafts) >= MAX_DRAFTS_PER_TURN:
            break
    return drafts


def record(ctx: GitContext, drafts: list[Draft]) -> int:
    path = store.tier_b(ctx, INBOX_FILE)
    for draft in drafts:
        store.append_jsonl(path, draft.to_dict())
    return len(drafts)


def pending(ctx: GitContext) -> list[dict]:
    """Drafts not yet accepted or discarded, newest first."""
    resolved: set[str] = set()
    items: list[dict] = []
    for entry in store.read_jsonl(store.tier_b(ctx, INBOX_FILE)):
        if not isinstance(entry, dict):
            continue
        if entry.get("resolved"):
            resolved.add(str(entry.get("quote", ""))[:80].lower())
            continue
        items.append(entry)
    out = [i for i in items if str(i.get("quote", ""))[:80].lower() not in resolved]
    out.sort(key=lambda i: float(i.get("created_at", 0)), reverse=True)
    return out


def resolve(ctx: GitContext, quote: str) -> None:
    """Mark a draft handled. Append-only: nothing is ever rewritten in place."""
    store.append_jsonl(
        store.tier_b(ctx, INBOX_FILE),
        {"quote": quote, "resolved": True, "created_at": time.time()},
    )


def render(draft: dict) -> str:
    """A record that is already most of the way written.

    The quote goes in verbatim under Why, because the founder's own words are the part
    that cannot be reconstructed later, and paraphrasing them is how a decision record
    turns into a summary nobody trusts.
    """
    paths = [s.get("path") for s in (draft.get("subject_paths") or []) if isinstance(s, dict)]
    scope = ", ".join(paths[:4]) if paths else "src/**"
    return "\n".join(
        [
            "---",
            f"title: <name the decision in five words>",
            f"paths: {scope}",
            f"date: {time.strftime('%Y-%m-%d', time.gmtime(draft.get('created_at', time.time())))}",
            "---",
            "",
            "## Decision",
            "<what was chosen>",
            "",
            "## Why",
            f"> {draft.get('quote', '')}",
            "",
            "## Rejected",
            "- <alternative>: <why it lost — the specific reason, not 'worse'>",
            "",
        ]
    )


def next_number(ctx: GitContext) -> int:
    from . import knowledge

    existing = knowledge.decision_files(ctx)
    numbers = [int(p.name.split("-", 1)[0]) for p in existing if p.name[:4].isdigit()]
    return (max(numbers) + 1) if numbers else 1
