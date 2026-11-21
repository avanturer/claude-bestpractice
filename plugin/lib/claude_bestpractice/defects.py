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

**It must not send anything on its own** for anyone but the owner. Filing a GitHub issue
uses the installer's own credentials and posts publicly under their name, in a repository
they do not own, carrying whatever the report holds. Nobody installing a plugin expects
that, so for them the default is capture-and-hold. Where `gh` files as the owner of the
repository the reports go to, consent is real — they own both ends — and a report is filed
the moment it is captured: held, fifty piled up unread for a month and went in one burst,
most of them about defects fixed long before (#313–#362).

The network never touches a hook. That is the property the five-hour-limit audit rests
on: a usage limit, an outage or an expired token cannot reach the gates, because the gates
never call anything. Sending lives in a CLI, which is the only place allowed to be slow
and the only place allowed to fail.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import store
from .gitctx import GitContext

DEFECTS_FILE = "defects.jsonl"

# Where `claude-bp-report send` holds its lock: on the machine, not in a clone, because the
# machine's own store (below) is sent from every clone and from outside them all.
SEND_LOCK = "defects-send.lock"
REPORT_REPO = "avanturer/claude-bestpractice"

# Enough to see a pattern, few enough that a gate stuck in a crash loop cannot fill a
# disk. Deduplication does most of the work; this is the backstop for the rest.
MAX_DEFECTS = 50

MAX_ERROR_CHARS = 300

# What a session writes about a wrong refusal is the whole of the evidence: no frame, no
# exception. Cut at the crash limit, #367 and #368 reached GitHub stopped mid-sentence, and
# #368 without the part that said which session was refused and why it held no card.
MAX_OBSERVATION_CHARS = 2000

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


def sanitize(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """What is safe to put in a public issue: no credentials, no directory layout, no more
    than `limit` characters.

    Scrubbed with the same pass the pre-write gate uses, then stripped of absolute paths.
    The report is worth nothing if it cannot be shown to a stranger, and a report that
    carries a founder's tree — or worse, a token that happened to be in an error string —
    is not a report, it is a leak with a title.
    """
    from . import redact

    cleaned = redact.scrub(redact.strip_control(text))
    return _ABSOLUTE.sub(lambda m: _shorten(m.group(0)), cleaned)[:limit]


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


# A report is kept in the clone it was made in, and one made outside every repository in the
# plugin's own directory on this machine (`selfupdate.data_dir`), which outlives its versions.
# A session standing in a folder that is no repository was told by a refusal to run
# `claude-bp-report defect`, and the command answered "not inside a git repository" (#379).
# `ctx` None below is that place: outside every repository.


def machine_store(home: Path | None = None) -> Path:
    """The store of the reports made outside every repository on this machine."""
    from . import selfupdate

    return selfupdate.data_dir(home) / DEFECTS_FILE


def _store(ctx: GitContext | None) -> Path:
    """Where a report made at `ctx` is written."""
    return store.tier_b(ctx, DEFECTS_FILE) if ctx is not None else machine_store()


def send_lock(home: Path | None = None) -> Path:
    """The one lock every sender on this machine takes, from a clone or from outside them."""
    from . import selfupdate

    return store.ensure_dir(selfupdate.data_dir(home)) / SEND_LOCK


def record(ctx: GitContext | None, gate: str, exc: BaseException) -> None:
    """File a crash, or count it again if this one has been seen before.

    Never raises. This runs inside the handler that is already dealing with a failure, and
    a reporter that throws while reporting turns one defect into two.
    """
    try:
        error = sanitize(f"{type(exc).__name__}: {exc}")
        where = _origin(exc)
        _file(ctx, signature(gate, error.split(":")[0], where), gate, error, where)
        if mode(ctx) == AUTO:
            send_soon(ctx)
    except Exception:  # noqa: BLE001 - a reporter that throws makes one defect into two
        return


def observed(ctx: GitContext | None, what: str, gate: str = "",
             refused: str = "") -> tuple[bool, str]:
    """A gate that refused the wrong thing, filed by the session it refused, with `refused`,
    the first line of the refusal that session last got, where the caller knows it.

    The other half of `record`. A crash files itself; a gate that runs perfectly and
    refuses the wrong call cannot, because nothing failed — and that is the whole of this
    plugin's defect history: an SQL operator read as a file path, a React variable read as
    a credential, a merged pull request reported as open. Every one was found by a person
    hitting it and writing it up, and the session that hit it first said nothing because
    saying it meant interrupting the founder with a question about a hook.

    So the session files it and carries on. The founder reads a count on a surface they
    already look at, `claude-bp-report` shows exactly what would be sent, and nothing
    leaves this machine without `send` — the same consent rule crash reports have.

    Returns whether it was recorded and what to print — the first half because the
    command exited 0 over a report it refused, and a session reads 0 as "filed". Never
    raises, and holds nothing back for judgement: a report that is wrong costs one line in
    a list the founder can clear.
    """
    try:
        chosen = mode(ctx)
        if chosen == OFF:
            return False, "defect capture is off (`report_defects`), so nothing was recorded"
        text = sanitize(_one_line(what), MAX_OBSERVATION_CHARS)
        if len(text) < MIN_OBSERVATION_CHARS:
            return False, ("say what was refused and why that is wrong — a report that names "
                           "neither cannot be acted on")
        named = _one_line(gate)[:40] or UNNAMED_GATE
        _file(ctx, signature(named, text, OBSERVED), named, text, OBSERVED, kind=OBSERVED,
              refused=sanitize(_one_line(refused)))
        if chosen == AUTO:
            send_soon(ctx)
            return True, f"recorded against {named}, and being filed at {REPORT_REPO}"
        return True, f"recorded against {named} — `claude-bp-report` shows what would be sent"
    except Exception:  # noqa: BLE001 - a reporter that throws makes one defect into two
        return False, "the report could not be written"


def mode(ctx: GitContext | None) -> str:
    """`local`, `auto` or `off`: the founder's `report_defects` where they wrote one.

    Unwritten, it is `auto` where this machine's `gh` files as the owner of the repository
    the reports go to, and `local` everywhere else. Outside every repository nobody wrote one.
    """
    from . import config

    if ctx is not None and config.is_set(ctx, "report_defects"):
        return config.load(ctx).report_defects
    owner = REPORT_REPO.split("/", 1)[0].lower()
    return AUTO if gh_account().lower() == owner else LOCAL


def gh_account() -> str:
    """The github.com account `gh` files issues as, read from its own config, else empty.

    Local, so a hook may ask: the active account is the host's `user` in `hosts.yml`, in
    the directory gh reads (`GH_CONFIG_DIR`, `XDG_CONFIG_HOME/gh`, `%AppData%/GitHub CLI`,
    `~/.config/gh`). A token in the environment decides the account instead, and says
    nothing here about whose it is.
    """
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return ""
    if os.environ.get("GH_CONFIG_DIR"):
        where = Path(os.environ["GH_CONFIG_DIR"])
    elif os.environ.get("XDG_CONFIG_HOME"):
        where = Path(os.environ["XDG_CONFIG_HOME"]) / "gh"
    elif os.name == "nt" and os.environ.get("APPDATA"):
        where = Path(os.environ["APPDATA"]) / "GitHub CLI"
    else:
        where = Path.home() / ".config" / "gh"
    try:
        text = (where / "hosts.yml").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    return _active_user(text, "github.com")


def _active_user(hosts_yml: str, host: str) -> str:
    """`user` directly under `host`, not one nested in its `users` map of every account."""
    inside, depth = False, None
    for line in hosts_yml.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            inside, depth = line.split(":", 1)[0].strip().strip("'\"") == host, None
            continue
        depth = indent if inside and depth is None else depth
        key, _, value = line.strip().partition(":")
        if inside and indent == depth and key == "user":
            return value.strip().strip("'\"")
    return ""


def send_soon(ctx: GitContext | None) -> None:
    """Start `claude-bp-report send` and walk away, when there is something to file.

    Detached and never waited on: a hook must not pay for the network or fail because of
    it, and what is not filed now stays captured for the next try.
    """
    if not unsent(ctx):
        return
    sender = Path(__file__).resolve().parents[2] / "bin" / "claude-bp-report"
    try:
        subprocess.Popen(
            [sys.executable, str(sender), "send"],
            cwd=str(ctx.worktree_root if ctx is not None else Path.home()),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return


def _file(ctx: GitContext | None, key: str, gate: str, error: str, where: str,
          kind: str = CRASH, refused: str = "") -> None:
    """Append one report, or count an identical one again. `refused` is the gate's own line,
    kept for a session-filed report.

    The counting is why both kinds share this: a report seen five times has to keep its
    first sighting and its sent state, and two copies of that arithmetic are two places
    for the count to be wrong.
    """
    path = _store(ctx)
    prior = next((r for r in _latest(path) if r.get("signature") == key), None)
    now = time.time()
    store.append_jsonl(path, {
        "signature": key,
        "kind": kind,
        "gate": gate,
        "error": error,
        "where": where,
        **({"refused": refused} if refused else {}),
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


# The key a loaded report carries naming the store it was read from, so `mark_sent` writes the
# sent row beside it. Never written to a store.
KEPT_IN = "_kept_in"


def _latest(path: Path) -> list[dict[str, Any]]:
    """The latest record per signature in one store. Append-only, so a later row wins."""
    latest: dict[str, dict[str, Any]] = {}
    for row in store.read_jsonl(path):
        if isinstance(row, dict) and row.get("signature"):
            latest[str(row["signature"])] = {**row, KEPT_IN: str(path)}
    return list(latest.values())


def _one_report(one: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """One report kept in both stores, as one: the later sighting, sent if either copy was.
    Listed twice, one `send` filed it twice (independent review of 2.20.0)."""
    def at(row: dict[str, Any], key: str) -> float:
        return float(row.get(key) or 0)

    later = other if at(other, "last_at") >= at(one, "last_at") else one
    return {**later, "sent_at": max(at(one, "sent_at"), at(other, "sent_at"))}


def load(ctx: GitContext | None) -> list[dict[str, Any]]:
    """The latest record per signature, newest first: this clone's, and the ones made outside
    every repository on this machine, which are shown and sent from wherever the founder is."""
    paths = [_store(ctx)] + ([machine_store()] if ctx is not None else [])
    merged: dict[str, dict[str, Any]] = {}
    for row in (row for path in paths for row in _latest(path)):
        key = str(row["signature"])
        merged[key] = _one_report(merged[key], row) if key in merged else row
    rows = sorted(merged.values(), key=lambda r: float(r.get("last_at") or 0), reverse=True)
    return rows[:MAX_DEFECTS]


def unsent(ctx: GitContext | None) -> list[dict[str, Any]]:
    """What `send` files: not filed yet, and last seen on the version that runs now.

    A report says what one version did. Held unsent across upgrades, it says nothing about
    the code that runs now: one `send` filed fifty, captured over a month on 2.2.0 to
    2.14.3, most about defects that hand-filed issues had long since had fixed (#313–#362).
    A defect still there is seen again, and a sighting stamps the version it was seen on.
    """
    return [r for r in load(ctx) if not r.get("sent_at") and _seen_on_this_version(r)]


def held_back(ctx: GitContext | None) -> list[dict[str, Any]]:
    """Not filed, and not seen since an upgrade: kept for `claude-bp-report`, never sent."""
    return [r for r in load(ctx) if not r.get("sent_at") and not _seen_on_this_version(r)]


def _seen_on_this_version(report: dict[str, Any]) -> bool:
    return str(report.get("version") or "") == _version()


def mark_sent(ctx: GitContext | None, report: dict[str, Any], url: str = "") -> None:
    """Write `report` as sent, in the store it was read from."""
    kept = {k: v for k, v in report.items() if k != KEPT_IN}
    path = Path(report[KEPT_IN]) if report.get(KEPT_IN) else _store(ctx)
    store.append_jsonl(path, {**kept, "sent_at": time.time(), "url": url})


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
    refused = str(report.get("refused") or "")
    return "\n".join([
        f"`{report.get('gate')}` {headline}{repeats}.",
        "",
        "```",
        # In the block, where a backtick in it cannot end the formatting and no `@` notifies.
        *([f"refused: {refused}", ""] if watched and refused else []),
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


def line(ctx: GitContext | None) -> str:
    """One line, and only when there is something. Never injected — a surface, not context."""
    waiting = unsent(ctx)
    if not waiting:
        return ""
    gates = ", ".join(sorted({str(r.get("gate")) for r in waiting})[:3])
    return (
        f"{len(waiting)} plugin defect(s) captured ({gates}) — "
        "`claude-bp-report` to see them, `claude-bp-report send` to file them"
    )
