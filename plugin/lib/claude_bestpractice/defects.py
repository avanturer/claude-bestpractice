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


def signature(gate: str, error: str, where: str) -> str:
    """What makes two crashes the same crash: the gate, the exception type, the line.

    The MESSAGE is deliberately excluded. `KeyError: 'artifact_globs'` and
    `KeyError: 'test_command'` at the same line are one defect in one place, and keying on
    the message would file a fresh report for every value the data happened to take —
    which is how a gate stuck in a loop turns into fifty issues in a stranger's repository.
    """
    payload = "\x00".join([gate, error.split(":")[0], where])
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
        key = signature(gate, error, where)
        prior = next((r for r in load(ctx) if r.get("signature") == key), None)
        now = time.time()
        store.append_jsonl(store.tier_b(ctx, DEFECTS_FILE), {
            "signature": key,
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
    except Exception:  # noqa: BLE001 - a reporter that throws makes one defect into two
        return


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
    return f"{report.get('gate', 'gate')} crashed: {report.get('error', '')[:80]}"


def body(report: dict[str, Any]) -> str:
    """Short on purpose. Everything here is mechanical; nothing is a description.

    A long report from a machine is a long report nobody reads, and the parts a fix
    actually needs are the gate, the exception and the line. What is deliberately absent
    is as important as what is here: no repository name, no branch, no task statement, no
    file contents, no paths outside this plugin.
    """
    seen = int(report.get("seen", 1))
    repeats = f" (seen {seen}×)" if seen > 1 else ""
    return "\n".join([
        f"`{report.get('gate')}` crashed{repeats}.",
        "",
        "```",
        report.get("error", ""),
        f"  at {report.get('where') or 'unknown'}",
        "```",
        "",
        f"claude-bestpractice {report.get('version')} · "
        f"Python {report.get('python')} · {report.get('platform')}",
        "",
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
