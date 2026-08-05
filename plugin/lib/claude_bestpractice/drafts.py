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

# A policy is prose, and 400 characters cut the middle out of one. A founder laying out
# release rules for three app stores wrote 522, and the record kept 400 of them — ending
# mid-word, with the fragment presented under "## Why" as their own words. Tier B costs
# nothing here; the inbox listing already shows only the first 150.
MAX_DRAFT_CHARS = 1_000
MAX_DRAFTS_PER_TURN = 2

# Same wording the prompt gate uses, for the same reason: a fragment presented as the
# whole instruction is a claim the founder cannot check. Issue #41, in a second file.
TRUNCATED = " […truncated; this is not the whole instruction]"

# Ordered by strength. A turn matching a strong marker is far more likely to carry a
# durable decision than one matching a weak one, and only the strongest match is kept.
#
# Each marker carries its vocabulary in both languages, side by side, because the
# alternative was measured: the classifier was English-only, so a founder working in
# Russian had an inbox that was reliably EMPTY rather than reliably wrong — five markers
# out of five silent on the same instructions their English translations classified
# correctly. That is worse than the noise #44 removed, because an empty inbox reads
# exactly like a session that made no decisions.
#
# `\b` is Unicode-aware on `str` patterns, so it works on Cyrillic unchanged. `[её]`
# spellings are both accepted; Russian keyboards produce either.
_VOCABULARY: list[tuple[str, str]] = [
    (
        # Leads the table, because this is the least ambiguous decision record there is:
        # a founder saying "remember this" has already done the hard half of writing one.
        #
        # Every other marker here is CORRECTION-shaped — it fires on the moment a human
        # overruled the agent. That missed a whole class, and the commonest one: a
        # standing instruction, stated calmly, correcting nothing. «запомни навсегда»,
        # «на будущее», «правило для всех чатов», "from now on" all scored None, and so
        # did a 500-character message laying out release policy for three app stores.
        # The subsystem that exists to stop durable instructions being forgotten was
        # deaf to the exact sentence that says "do not forget this".
        #
        # `всегда`/`always` carry a verb list rather than standing alone, and that is a
        # deliberate precision choice with a known cost — #51 is about a verb list
        # missing natural phrasing. Bare `всегда` is far too common in description
        # ("это всегда падает") to file as policy, and the phrase branches above it
        # catch the general case without it.
        "standing",
        # Negation excluded explicitly: "I do not remember whether we shipped that" is a
        # question, not a policy, and the space before the verb is already consumed by the
        # boundary class, so the lookbehind lands exactly on the negation.
        r"(?:^|[\s(«\"])(?:(?<!not )(?<!n't )remember\b(?:\s+(?:this|that|forever))?|please remember\b|"
        r"from now on\b|going forward\b|as a rule\b|make it a rule\b|"
        r"standing (?:rule|instruction)\b|for (?:all|every) (?:chat|session|project|repo)s?\b)"
        r"|\balways\s+(?:use|write|do|keep|put|check|name|prefer|run|add|ship|tag)\b"
        r"|(?:^|[\s(«\"])(?:запомни|на будущее\b|впредь\b|с этого момента\b|"
        r"для всех (?:чатов|сессий|проектов)\b|во всех чатах\b|"
        r"(?:такое |новое |это )?правило(?: для|:)|"
        r"хочу,? что ?бы (?:всегда|везде))"
        r"|\b(?:всегда|по умолчанию)\s+(?:пиши|делай|используй|держи|ставь|проверяй|"
        r"указывай|добавляй|веди|называй|оформляй|ставим|делаем|пишем|держим)\b",
    ),
    (
        "decision",
        r"\b(?:we (?:decided|agreed|settled on)|let'?s go with|going with)\b"
        r"|\b(?:реш(?:или|ил|ила|ено)|договорились|остановились на|останов(?:имся|ились)|"
        r"бер[её]м|ид[её]м с|выбрали|выбираем|оста[её]мся на)\b",
    ),
    (
        # The English branch wanted `never` to be followed by a verb from a fixed list, so
        # "use Decimal here, never float" — the most natural way to say it — scored None.
        # A comma before `never` is the shape that carries the rejection, and it does not
        # fire on "I have never seen this".
        "rejection",
        r"\b(?:don'?t|do not|never|stop) (?:use|using|do|doing|add|adding)\b"
        r"|,\s*never\s+\w+"
        r"|\b(?:никогда не|больше не|не (?:использу(?:й|йте|ем)|надо|нужно|делай|дела(?:ем|йте))|"
        r"перестань|прекрати)\b",
    ),
    # The word boundary lives inside each branch. A trailing \b after a comma never
    # matches — comma to space is not a boundary — which silently kills the branch.
    (
        "correction",
        r"(?:^|[\s(«\"])(?:no,|actually,|not that\b|instead of\b|rather than\b)"
        # «или нет,» is the tail of a question — "ставили мы версию или нет" — and reading
        # it as a correction filed a draft for every time the founder wondered aloud.
        r"|(?:^|[\s(«\"])(?:(?<!или )нет,|не так\b|вместо (?:этого|того)\b|наоборот\b|не то\b)",
    ),
    (
        "constraint",
        r"\b(?:must (?:always|never)|cannot|has to be|required to)\b"
        r"|\b(?:должн(?:о|а|ы|ен) быть|обязательно|нельзя|только через)\b",
    ),
    (
        "rationale",
        r"\bbecause\b.{0,120}\b(?:cost|slow|break|broke|fail|risk|ops|team|legal)\b"
        r"|\b(?:потому что|иначе|из-за)\b.{0,120}"
        r"\b(?:слома|упад[её]т|поед(?:ет|ут)|пад[её]т|дорого|медленно|риск|тормоз)",
    ),
]

_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pattern, re.I)) for name, pattern in _VOCABULARY
]


# Turns that look like corrections but carry no durable decision. Filtering these is
# what keeps the inbox worth opening.
_NOISE = re.compile(
    r"^(?:no,? (?:thanks|thank you|worries|problem)|not (?:now|yet|today)|"
    r"actually,? never ?mind|stop\.?$|wait\.?$"
    r"|нет,? спасибо|не сейчас|неважно|не важно|ладно,? забудь|"
    r"стоп\.?$|погоди\.?$|подожди\.?$)",
    re.I,
)

# Not everything the transcript files under `type: "user"` came from a user. Claude Code
# feeds hook output, compaction preambles and interrupt markers back into the
# conversation as user records, and this plugin's own refusals are full of the words
# `classify` looks for — "not done yet", "must", a list of paths. So a blocked Stop wrote
# the gate's message into the inbox as a founder decision, and the loop fed itself: the
# more the gate blocked, the more "decisions" appeared. Measured on a live repository, 96
# drafts — 57 the gate quoting itself, 39 compaction boilerplate, 0 from a human.
_SYNTHETIC = re.compile(
    r"(?i)^(?:"
    r"stop hook feedback\b|"
    r"\[[^\]]*hook[^\]]*\]|"
    r"this session is being continued from a previous conversation\b|"
    r"\[request interrupted\b|"
    r"caveat: the messages below\b|"
    r"<(?:command-name|command-message|local-command-stdout|system-reminder|"
    r"user-prompt-submit-hook|bash-input|bash-stdout|bash-stderr)\b"
    r")"
)

# The plugin must never file its own voice as something the founder said, wherever in the
# turn it appears — a hook message quoted mid-record still has this in it.
_OWN_VOICE = "claude-bestpractice"


def is_synthetic(turn: str) -> bool:
    """Was this `type: "user"` record written by the harness rather than by a person?

    Deliberately textual. The transcript format is internal and a structural flag on
    injected records is not guaranteed to exist or to keep its name, so this degrades to
    string rules rather than raising — and the rules are anchored at the start of the
    record, where the harness puts its own prefixes, so a founder quoting one of these
    phrases mid-sentence is still heard.
    """
    stripped = turn.lstrip()
    return bool(_SYNTHETIC.match(stripped)) or _OWN_VOICE in turn.lower()


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
        if body and not is_synthetic(body):
            turns.append(body)
    return turns


def classify(turn: str) -> str | None:
    """The strongest marker this turn matches, or None."""
    if len(turn) < 25 or _NOISE.match(turn.strip()) or is_synthetic(turn):
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
        flat = " ".join(turn.split())
        quote = flat[:MAX_DRAFT_CHARS] + (TRUNCATED if len(flat) > MAX_DRAFT_CHARS else "")
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


def subject_paths(draft: dict) -> list[str]:
    """The files a draft is about, whichever shape they were stored in.

    Two shapes exist and only one was ever read. `extract` stores plain strings, taken
    from what the session touched; `provenance.stamp` stores dicts carrying a blob hash.
    `render` read dicts only, so for every real draft the list came back EMPTY and the
    record fell through to its `paths: src/**` default.

    That default is not a cosmetic loss. `paths:` is what stops a decision loading in
    sessions it has nothing to do with, and the validator refuses a record without one
    precisely because "no scope" means "every session". Claiming the whole source tree is
    the same thing said differently — so every accepted decision was global, and the
    scoping this layer's whole defence against bloat rests on had never once worked.
    """
    out: list[str] = []
    for entry in draft.get("subject_paths") or []:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def render(draft: dict) -> str:
    """A record that is already most of the way written.

    The quote goes in verbatim under Why, because the founder's own words are the part
    that cannot be reconstructed later, and paraphrasing them is how a decision record
    turns into a summary nobody trusts.
    """
    paths = subject_paths(draft)
    scope = ", ".join(paths[:4]) if paths else "src/**"
    return "\n".join(
        [
            "---",
            f"title: <name the decision in five words>",
            f"paths: {scope}",
            f"date: {time.strftime('%Y-%m-%d', time.gmtime(draft.get('created_at', time.time())))}",
            # Emitted empty rather than omitted. A decision is a historical fact and is
            # retired by a later record naming it here, never by rewriting the old one —
            # that is the design, and the index has honoured this field all along. What
            # was missing was any way to fill it: nothing in the plugin wrote it, so the
            # retirement path existed and was unreachable, and records piled up with
            # contradictions live alongside each other in every session's context.
            f"supersedes: {draft.get('supersedes', '')}",
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
