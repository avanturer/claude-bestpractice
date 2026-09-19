"""Defects in this plugin, caught where they happen and reported without costing a turn.

Every failure this plugin has fixed was found by someone hitting it and writing it up by
hand. That works because the founder who owns the repository is also the one hitting it.
It stops working the moment anyone else installs it: they hit the same defect, work
around it, and nothing here ever learns.

Two things must both be true, and they pull against each other.

**It must not cost the session anything.** A crash report the agent has to read, or
summarise, or decide about, is worse than the crash — it spends context on the plugin's
problems instead of the founder's work. So capture writes to disk and injects nothing.
The only visible trace is a count on a surface the founder already looks at.

**It must not send anything on its own.** Filing a GitHub issue uses the installer's own
credentials and posts publicly under their name, in a repository they do not own,
carrying whatever the report holds. Nobody installing a plugin expects that, and the
default is therefore capture-and-hold. `auto` exists for the one case where consent is
real — the owner running it on their own machines — and is a deliberate act of turning it
on, not a thing that happens to you.

The network never touches a hook. That is the property the five-hour-limit audit rests
on: a usage limit, an outage or an expired token cannot reach the gates, because the gates
never call anything. Sending lives in a CLI, which is the only place allowed to be slow
and the only place allowed to fail.
"""

from __future__ import annotations

import hashlib
import platform
import re
import sys
import time
from typing import Any

from . import store
from .gitctx import GitContext

DEFECTS_FILE = "defects.jsonl"
REPORT_REPO = "avanturer/claude-bestpractice"

# Enough to see a pattern, few enough that a gate stuck in a crash loop cannot fill a
# disk. Deduplication does most of the work; this is the backstop for the rest.
MAX_DEFECTS = 50

MAX_ERROR_CHARS = 300

# A report shorter than this says nothing a fix could use. Deliberately low: the cost of
# a thin report is one line the founder deletes, and the cost of a threshold that turns
# reporting into work is a session that goes back to interrupting them instead.
MIN_OBSERVATION_CHARS = 20

# Where a session-filed report sits in the `where` field, which for a crash holds the
# frame. There is no frame: nothing failed, the gate refused correctly as written.
OBSERVED = "observed"
CRASH = "crash"
UNNAMED_GATE = "gate"

# Modes. `local` captures and holds, and is the default because sending is not the
# plugin's call to make. `auto` also sends, and is only ever set by someone who owns both
# ends. `off` does not even capture.
LOCAL, AUTO, OFF = "local", "auto", "off"

# A path in an exception message is the founder's directory layout, which is theirs and
# is not needed to fix anything. The plugin's own files are kept, because they are the
# only part that says where the defect is.
_ABSOLUTE = re.compile(r"(?<![\w/])/(?:[\w.@+-]+/)+[\w.@+-]*")
_OURS = "claude_bestpractice"


def _shorten(path: str) -> str:
    """Keep our own files whole; erase everything else, basename included.

    Keeping the last segment was the first version, and it put `billing.py` — a filename
    out of a private repository — into a public issue whose own last line promised
    "nothing from the repository it ran in". For a defect in THIS plugin the founder's
    filename is never the useful part; the frame in our code is. So the claim is made
    true rather than softened.
    """
    if _OURS in path or "/plugin/bin/" in path:
        marker = path.find(_OURS)
        return path[marker:] if marker >= 0 else path.rsplit("/", 1)[-1]
    return "<path>"


def sanitize(text: str) -> str:
    """What is safe to put in a public issue: no credentials, no directory layout.

    Scrubbed with the same pass the pre-write gate uses, then stripped of absolute paths.
    The report is worth nothing if it cannot be shown to a stranger, and a report that
    carries a founder's tree — or worse, a token that happened to be in an error string —
    is not a report, it is a leak with a title.
    """
    from . import redact

    cleaned = redact.scrub(redact.strip_control(text))
    return _ABSOLUTE.sub(lambda m: _shorten(m.group(0)), cleaned)[:MAX_ERROR_CHARS]


def signature(gate: str, what: str, where: str) -> str:
    """What makes two reports the same report: the gate, what distinguishes it, the place.

    `what` is already reduced to the part that discriminates, by the caller that knows
    which part that is. For a crash it is the exception TYPE and never the message:
    `KeyError: 'artifact_globs'` and `KeyError: 'test_command'` at the same line are one
    defect in one place, and keying on the message would file a fresh report for every
    value the data happened to take — which is how a gate stuck in a loop turns into
    fifty issues in a stranger's repository. For a refusal a session reports, it is the
    sentence the session wrote, because two different wrong refusals from one gate are
    two defects.
    """
    payload = "\x00".join([gate, what, where])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:12]


def _origin(exc: BaseException) -> str:
    """The deepest frame inside this plugin — where the defect actually is.

    The last frame is often in the standard library, which says what broke and not whose
    fault it is. Ours is the one a fix has to touch.
    """
    tb = exc.__traceback__
    found = ""
    while tb is not None:
        name = tb.tb_frame.f_code.co_filename
        if _OURS in name or "/bin/" in name:
            found = f"{_shorten(name)}:{tb.tb_lineno}"
        tb = tb.tb_next
    return found


def record(ctx: GitContext, gate: str, exc: BaseException) -> None:
    """File a crash, or count it again if this one has been seen before.

    Never raises. This runs inside the handler that is already dealing with a failure, and
    a reporter that throws while reporting turns one defect into two.
    """
    try:
        error = sanitize(f"{type(exc).__name__}: {exc}")
        where = _origin(exc)
        _file(ctx, signature(gate, error.split(":")[0], where), gate, error, where)
    except Exception:  # noqa: BLE001 - a reporter that throws makes one defect into two
        return


def observed(ctx: GitContext, what: str, gate: str = "") -> str:
    """A gate that refused the wrong thing, filed by the session it refused.

    The other half of `record`. A crash files itself; a gate that runs perfectly and
    refuses the wrong call cannot, because nothing failed — and that is the whole of this
    plugin's defect history: an SQL operator read as a file path, a React variable read as
    a credential, a merged pull request reported as open. Every one was found by a person
    hitting it and writing it up, and the session that hit it first said nothing because
    saying it meant interrupting the founder with a question about a hook.

    So the session files it and carries on. The founder reads a count on a surface they
    already look at, `claude-bp-report` shows exactly what would be sent, and nothing
    leaves this machine without `send` — the same consent rule crash reports have.

    Returns what to print, never raises, and holds nothing back for judgement: a report
    that is wrong costs one line in a list the founder can clear.
    """
    try:
        from . import config

        if config.load(ctx).report_defects == OFF:
            return "defect capture is off (`report_defects`), so nothing was recorded"
        text = sanitize(_one_line(what))
        if len(text) < MIN_OBSERVATION_CHARS:
            return ("say what was refused and why that is wrong — a report that names "
                    "neither cannot be acted on")
        named = _one_line(gate)[:40] or UNNAMED_GATE
        _file(ctx, signature(named, text, OBSERVED), named, text, OBSERVED, kind=OBSERVED)
        return f"recorded against {named} — `claude-bp-report` shows what would be sent"
    except Exception:  # noqa: BLE001 - a reporter that throws makes one defect into two
        return "the report could not be written"


def _file(ctx: GitContext, key: str, gate: str, error: str, where: str,
          kind: str = CRASH) -> None:
    """Append one report, or count an identical one again.

    The counting is why both kinds share this: a report seen five times has to keep its
    first sighting and its sent state, and two copies of that arithmetic are two places
    for the count to be wrong.
    """
    prior = next((r for r in load(ctx) if r.get("signature") == key), None)
    now = time.time()
    store.append_jsonl(store.tier_b(ctx, DEFECTS_FILE), {
        "signature": key,
        "kind": kind,
        "gate": gate,
        "error": error,
        "where": where,
        "version": _version(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": platform.system().lower(),
        "first_at": float(prior.get("first_at", now)) if prior else now,
        "last_at": now,
        "seen": int(prior.get("seen", 0)) + 1 if prior else 1,
        "sent_at": float(prior.get("sent_at", 0)) if prior else 0.0,
    })


def _one_line(text: str) -> str:
    """Collapse to one line, so a report cannot be read as several fields."""
    return " ".join(str(text).split())


def _version() -> str:
    from . import __version__

    return __version__


def load(ctx: GitContext) -> list[dict[str, Any]]:
    """The latest record per signature, newest first. Append-only, so a later row wins."""
    latest: dict[str, dict[str, Any]] = {}
    for row in store.read_jsonl(store.tier_b(ctx, DEFECTS_FILE)):
        if isinstance(row, dict) and row.get("signature"):
            latest[str(row["signature"])] = row
    rows = sorted(latest.values(), key=lambda r: float(r.get("last_at") or 0), reverse=True)
    return rows[:MAX_DEFECTS]


def unsent(ctx: GitContext) -> list[dict[str, Any]]:
    return [r for r in load(ctx) if not r.get("sent_at")]


def mark_sent(ctx: GitContext, report: dict[str, Any], url: str = "") -> None:
    store.append_jsonl(
        store.tier_b(ctx, DEFECTS_FILE), {**report, "sent_at": time.time(), "url": url}
    )


def title(report: dict[str, Any]) -> str:
    verb = "refused wrongly" if report.get("kind") == OBSERVED else "crashed"
    return f"{report.get('gate', 'gate')} {verb}: {report.get('error', '')[:80]}"


def body(report: dict[str, Any]) -> str:
    """Short on purpose. Everything here is mechanical; nothing is a description.

    A long report from a machine is a long report nobody reads, and the parts a fix
    actually needs are the gate, the exception and the line. What is deliberately absent
    is as important as what is here: no repository name, no branch, no task statement, no
    file contents, no paths outside this plugin.
    """
    seen = int(report.get("seen", 1))
    repeats = f" (seen {seen}×)" if seen > 1 else ""
    watched = report.get("kind") == OBSERVED
    headline = "refused the wrong thing" if watched else "crashed"
    return "\n".join([
        f"`{report.get('gate')}` {headline}{repeats}.",
        "",
        "```",
        report.get("error", ""),
        *([] if watched else [f"  at {report.get('where') or 'unknown'}"]),
        "```",
        "",
        f"claude-bestpractice {report.get('version')} · "
        f"Python {report.get('python')} · {report.get('platform')}",
        "",
        "_Filed by the plugin's own defect reporter, from a session this gate refused._"
        if watched else
        "_Filed by the plugin's own defect reporter. It carries the gate, the exception "
        "and the line, and nothing from the repository it ran in._",
    ])


def line(ctx: GitContext) -> str:
    """One line, and only when there is something. Never injected — a surface, not context."""
    waiting = unsent(ctx)
    if not waiting:
        return ""
    gates = ", ".join(sorted({str(r.get("gate")) for r in waiting})[:3])
    return (
        f"{len(waiting)} plugin defect(s) captured ({gates}) — "
        "`claude-bp-report` to see them, `claude-bp-report send` to file them"
    )
