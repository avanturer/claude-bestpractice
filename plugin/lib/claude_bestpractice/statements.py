"""Whether a message the founder sent is a statement of work, and whether it opens a card.

Read by `prompt-capture`, which records the statement, and by the repair that takes back the
cards older versions opened from messages that were never work. One reader, so the two can
never disagree about what an instruction looks like.

The cost of a wrong answer is asymmetric everywhere in this module. Refusing a real
instruction leaves the previous statement standing, and a card is still demanded at the
first write — named by the session doing the work. Accepting a remark writes it onto the
board, where every sibling reads it as somebody's job.
"""

from __future__ import annotations

import re

from . import config, inbox
from .founder import outside_markup

# Most turns in a real session are not statements of work — they are the founder saying
# "keep going". Taking the last turn unconditionally meant the field that names what a
# session is doing held the least informative sentence they typed. Measured on a live
# repository: three concurrent sessions whose tasks read «Делай», «обнови», and a merge
# question, with the second provisioning a branch called `feat/obnovi-70e44134`. The
# transliteration is its own trap — «Делай» renders as `delay`, an English word meaning
# the opposite of what the session was doing.
#
# The deny-list carries the work; the floor is only a backstop for acknowledgements
# nobody thought of. Set where it clears every entry on the list and still admits a
# terse but real instruction ("почини баг в auth").
MIN_STATEMENT_CHARS = 16

_CONTINUATION = re.compile(
    r"(?i)^(?:"
    r"делай|давай|сделай|поехали|начинай|продолжай|продолжи|дальше|ещё|еще|"
    r"обнови|обновись|проверь|исправь|го|ок|окей|окей\.|ага|да|ну|хорошо|"
    r"супер|отлично|спасибо|пж|плиз|"
    r"go|go ahead|ok|okay|k|yes|yep|yeah|sure|please|thanks|thx|"
    r"continue|proceed|next|do it|just do it|keep going|carry on|"
    r"fix it|update|retry|again|more"
    r")$"
)


# Every spelling this plugin speaks in. A message of its own arriving back as the session's
# task statement is the plugin quoting itself as the thing to be measured against — reported
# from a session where the drift block did exactly that.
#
# Refusals begin `claude-bestpractice:`; a delivered fact carries an inbox marker. The second
# was missing once, and the note — `[claude-bestpractice] another session is blocked on
# store.py…` — is long and names a path, so it passed every test this file applies for a
# statement of work. The plugin's own fact became the recipient's task: quoted back by every
# drift refusal, used to name their branch, adding ITS paths to their scope. #106, #118 and
# #166, through the one door this plugin built itself, because two markers for one voice had
# drifted apart by one character.
#
# So: imported rather than spelled a second time, and ALL of `inbox.MARKERS` rather than the
# current one. A rename does not reach the sessions already running — while an upgrade lands,
# a sibling on the previous release still delivers with the marker it knows, and this reader
# is the only thing between that note and the same defect.
_OUR_OWN_VOICE = "claude-bestpractice:"
_OUR_VOICES = (_OUR_OWN_VOICE, *inbox.MARKERS)


def is_harness_block(text: str) -> bool:
    """Was this written by the harness rather than by the founder?

    A name list cannot answer this, and trying was the defect: `background-task` was on it
    and `task-notification` was not, so a background-task completion notice became the
    session's task statement — and the scope-drift refusal then quoted a tool-use id back
    at an agent that had no way to satisfy it, in the middle of unattended overnight work
    (#118). This asks about the SHAPE, which does not change when the harness adds a block.

    Nothing is stripped on the strength of this. It decides only whether the text may
    become the record of what the founder asked for.

    Markup and nothing else, in as many elements as it comes: a founder does not type that —
    when they paste XML they paste it INTO a sentence. One element was the whole test, so
    two of them side by side, which is what `!` shell mode records for a command's output,
    read as a founder's message.
    """
    stripped = text.strip()
    if not stripped:
        return False
    return (stripped.startswith(_OUR_VOICES) or not outside_markup(stripped).strip()
            or is_a_notice(stripped))


def is_statement_of_work(prompt: str, paths: list[str]) -> bool:
    """Does this prompt say what the session is doing, or only that it should carry on?

    Naming a file settles it: a prompt that points at code is about that code however
    short it is. Otherwise a bare continuation is rejected outright, and everything else
    has to clear a length floor — a rule that cannot be exactly right and does not need
    to be, because the cost of a wrong answer is asymmetric. Rejecting a real instruction
    leaves the previous one standing, which is stale; accepting «ок» destroys the only
    record of what this session is for, and that record is committed on an unverified
    finish.
    """
    text = " ".join(prompt.split())
    if is_pasted_output(prompt) or is_harness_block(prompt):
        return False
    # A switch line is the founder's word on a gate, and the reader that wants it has
    # already taken it a few lines above. Struck out here rather than merely ranked below
    # a real statement: `worktree_setup bash infra/scripts/worktree_db_init.sh` names a
    # path, and naming a path is what settles this question first (#166).
    if config.is_only_a_switch(prompt):
        return False
    if paths:
        return True
    if _CONTINUATION.match(text.strip(" .!?,…").lower()):
        return False
    return len(text) >= MIN_STATEMENT_CHARS


# A shell prompt, a traceback, a log line. Pasting output is showing the session something,
# never instructing it.
_PASTED = (
    # `[$#]` then whitespace OR end of line: scrubbing strips trailing spaces, so the
    # final `user@host:~$ ` of a pasted session lost the space that anchored this and
    # stopped counting — leaving one hit where there were two, and the paste read as an
    # instruction again.
    re.compile(r"^\s*(?:\(\S+\)\s*)?[\w.-]+@[\w.-]+:[^\n]*[$#](?:\s|$)", re.M),
    re.compile(r"^\s*Traceback \(most recent call last\):", re.M),
    re.compile(r"^\s*(?:npm ERR!|error TS\d|FAILED |=+ (?:FAILURES|ERRORS) =+)", re.M),
    re.compile(r"^\s*\[\d{4}-\d\d-\d\d[T ]\d\d:\d\d", re.M),
)


def is_pasted_output(prompt: str) -> bool:
    """Terminal output pasted as a message, which is not a statement of work.

    The founder pastes the tail of a deploy run; it becomes the session's task; every
    scope-drift refusal then quotes `Task was: (.venv) hedge@AVANTURER-PC:~/dev/...` back
    at an agent that cannot possibly satisfy it, and the founder is pulled in to type a
    filename before any further work can be reported (#106).

    The FIRST line decides, because that is where an instruction lives. "this failed, look:
    <traceback>" is an instruction with evidence attached and must stay one; a ratio rule
    got that wrong on exactly the length the founder actually types.
    """
    lines = [line for line in prompt.splitlines() if line.strip()]
    if not lines or not any(pattern.match(lines[0]) for pattern in _PASTED):
        return False
    hits = sum(1 for line in lines if any(pattern.match(line) for pattern in _PASTED))
    return hits >= 2 or len(lines) == 1


# The harness also speaks in plain text, and says so: Claude Code 2.1.286 delivers
# `[Cross-session idle notice] "fuddy-8b", which you asked to be notified about, is idle now
# … This is an automated notice from that session's harness — not a message from a person`
# as a prompt of its own, and it became a card on the board (#256). A bracketed tag opening
# the message AND the notice's own word that it is automated: a founder writing "[срочно]"
# says nothing of the kind.
_NOTICE_TAG = re.compile(r"^\[[^\]\n]{3,80}\]")
_SAYS_IT_IS_AUTOMATED = re.compile(r"(?i)\bautomated notice\b|\bnot a message from a person\b")


def is_a_notice(text: str) -> bool:
    """A plain-text notice the harness wrote, saying of itself that it is not a person."""
    stripped = text.strip()
    return bool(_NOTICE_TAG.match(stripped) and _SAYS_IT_IS_AUTOMATED.search(stripped))


# A question asks for an answer, not for work: «тебя можно закрывать?», «что осталось
# сделать». Twenty-one of one repository's 156 open cards were questions and remarks like
# these, a real task among them renamed by one (#256). Only words that ask and nothing else
# open a question here — «как» and "how" also start "как обычно, почини тесты".
_ASKING = re.compile(
    r"(?i)^(?:что|чего|почему|зачем|где|когда|сколько|кто|какой|какая|какое|какие|чей|"
    r"what|why|where|when|which|who)\b"
)


def is_a_question(text: str) -> bool:
    """Does this message ask something rather than ask for something?"""
    flat = " ".join(text.split())
    return flat.endswith("?") or bool(_ASKING.match(flat))


def opens_a_card(prompt: str, paths: list[str]) -> bool:
    """Is this message work enough to go on the board as a card?

    A statement of work that is not a question. Naming a file still settles it, as it does
    for the statement: «почему падает tests/test_api.py?» is about that file.
    """
    return is_statement_of_work(prompt, paths) and (bool(paths) or not is_a_question(prompt))
