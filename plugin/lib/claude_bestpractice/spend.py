"""What the sessions of this repository spent, read off the transcripts Claude Code keeps.

A request to the model carries the whole conversation so far. Most of it is read from the
prompt cache at a tenth of the price, and it is still read on every request: in one session
of this plugin's own, 271 requests with a mean context of 478,000 tokens, 73% of the spend
was that re-reading and 64% of the spend fell on requests past 400,000 of context. Nothing
showed it: a ceiling on the context (`autoCompactWindow`) is a setting nobody had set, and
Fable, Sonnet 5 and Opus 4.7 onward compact only near 967,000.

This reads every request once and says where the spend went, and what the same conversation
would have cost compacted at a ceiling. Read-only, and nothing here is sent to the model.

Spend is in input-token equivalents at list-price ratios: fresh input 1, a cache write 1.25
(five minutes) or 2 (one hour), a cache read 0.1, output 5.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .gitctx import GitContext

READ, WRITE_5M, WRITE_1H, FRESH, OUT = 0.1, 1.25, 2.0, 1.0, 5.0

# The ceilings a history is replayed under. `autoCompactWindow` takes 100,000 to 1,000,000.
CEILINGS = (200_000, 300_000, 400_000)
# Where a context's size is counted: under, between and past the ceilings above.
BANDS = ((0, 100_000), (100_000, 200_000), (200_000, 400_000), (400_000, 0))

# What a compaction costs in a replay: the summary it writes, and what it leaves in the
# conversation besides the always-on part: that summary and the files Claude Code reads again.
SUMMARY_OUT = 5_000
KEPT = 25_000

# Most of a prefix written again in the middle of a session: its cache had expired, or a
# compaction or a changed prefix rebuilt it.
REWRITTEN_SHARE = 0.5
SMALLEST_REWRITE = 20_000


@dataclass
class Turn:
    """One request to the model, as its `usage` says it: what was read, written and answered."""

    fresh: int = 0
    write_5m: int = 0
    write_1h: int = 0
    read: int = 0
    out: int = 0
    side: bool = False

    @property
    def written(self) -> int:
        return self.write_5m + self.write_1h

    @property
    def context(self) -> int:
        return self.fresh + self.written + self.read

    @property
    def spend(self) -> float:
        return (self.fresh * FRESH + self.write_5m * WRITE_5M + self.write_1h * WRITE_1H
                + self.read * READ + self.out * OUT)


@dataclass
class Report:
    """What a set of transcripts adds up to. Every share is of `spend`."""

    transcripts: int = 0
    conversations: int = 0
    requests: int = 0
    by_agents: int = 0
    spend: float = 0.0
    kinds: dict = field(default_factory=dict)
    bands: dict = field(default_factory=dict)
    floors: list = field(default_factory=list)
    peaks: list = field(default_factory=list)
    rewrites: int = 0
    rewritten: int = 0
    replayed: dict = field(default_factory=dict)
    compactions: dict = field(default_factory=dict)


def turns(path: Path) -> list[Turn]:
    """The requests of one transcript, each once and in the order they were made.

    A reply is written as one line per block of it, every line carrying the same `usage`
    under the same message id: counted per line, a reply of three tool calls was three
    requests.
    """
    seen: set = set()
    found: list[Turn] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                turn = _turn(line, seen, "subagents" in path.parts)
                if turn is not None:
                    found.append(turn)
    except OSError:
        return []
    return found


def _turn(line: str, seen: set, in_an_agents_file: bool) -> Turn | None:
    """The request one transcript line records, or None: no usage on it, a line of a reply
    already counted, or a message the harness wrote itself (`<synthetic>`)."""
    row = _assistant_row(line)
    if row is None:
        return None
    message = row["message"]
    ident = message.get("id") or row.get("requestId") or row.get("uuid")
    if ident in seen or str(message.get("model") or "").startswith("<"):
        return None
    seen.add(ident)
    return _read_usage(message["usage"], in_an_agents_file or bool(row.get("isSidechain")))


def _assistant_row(line: str) -> dict | None:
    """A transcript line as the model's reply it records, with its `usage`, or None."""
    if '"usage"' not in line:
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    message = row.get("message") if isinstance(row, dict) else None
    if not isinstance(message, dict) or row.get("type") != "assistant":
        return None
    return row if isinstance(message.get("usage"), dict) else None


def _read_usage(usage: dict, side: bool) -> Turn:
    written = _count(usage.get("cache_creation_input_tokens"))
    split = usage.get("cache_creation")
    split = split if isinstance(split, dict) else {}
    hour = _count(split.get("ephemeral_1h_input_tokens"))
    five = _count(split.get("ephemeral_5m_input_tokens"))
    if hour + five != written:
        # No split given, or one that does not add up: priced as the cheaper write.
        five, hour = written, 0
    return Turn(_count(usage.get("input_tokens")), five, hour,
                _count(usage.get("cache_read_input_tokens")), _count(usage.get("output_tokens")),
                side)


def _count(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def replay(history: list[Turn], ceiling: int) -> tuple[float, int]:
    """What one conversation would have spent compacted whenever it passed `ceiling`, and how
    many compactions that takes. 0 for no ceiling.

    Each request adds what it added in fact. A compaction reads the whole context once,
    writes a summary, and leaves the always-on part (the first request's context) plus
    `KEPT`. With no ceiling this comes out near what was spent in fact, short of the
    prefixes written again after an expired cache: that is the check on the model, and
    `Report.replayed[0]` carries it.
    """
    spent, context, before, count, floor = 0.0, 0, 0, 0, 0
    for turn in history:
        added, context = _grown(turn, before, context, ceiling)
        floor = floor or turn.context
        before = turn.context
        if ceiling and context > ceiling:
            spent += (context - added) * READ + SUMMARY_OUT * OUT + KEPT * WRITE_1H
            context = floor + KEPT + added
            count += 1
        spent += _spent_on(turn, context, added)
    return spent, count


def _grown(turn: Turn, before: int, context: int, ceiling: int) -> tuple[int, int]:
    """(what this request added to the conversation, the replayed context with it)."""
    if not before:
        return turn.fresh + turn.written, turn.context
    if turn.context >= before:
        added = turn.context - before
        return added, context + added
    # Compacted in fact: what it wrote is what it added, and with no ceiling the replay
    # follows the conversation down.
    added = min(turn.fresh + turn.written, turn.context)
    return added, (context if ceiling else turn.context - added) + added


def _spent_on(turn: Turn, context: int, added: int) -> float:
    written = min(added, turn.written)
    price = WRITE_1H if turn.write_1h else WRITE_5M
    return ((context - added) * READ + written * price + (added - written) * FRESH
            + turn.out * OUT)


def measure(files: list[Path]) -> Report:
    """Every transcript in `files`, added up."""
    report = Report(transcripts=len(files), kinds=dict.fromkeys(
        ("fresh input", "cache write (5 min)", "cache write (1 h)", "cache read", "output"), 0.0),
        bands={band: 0.0 for band in BANDS},
        replayed=dict.fromkeys((0, *CEILINGS), 0.0), compactions=dict.fromkeys(CEILINGS, 0))
    for path in files:
        history = turns(path)
        if history:
            _add(report, history)
    return report


def _add(report: Report, history: list[Turn]) -> None:
    own = [turn for turn in history if not turn.side]
    report.conversations += 1
    report.requests += len(history)
    report.by_agents += len(history) - len(own)
    report.peaks.append(max(turn.context for turn in history))
    if own:
        report.floors.append(own[0].context)
    for at, turn in enumerate(history):
        _count_turn(report, turn, first=at == 0)
    # A subagent's transcript is a conversation of its own, under the same ceiling. Agent
    # turns written into a session's own file are not one: they stay as they were spent.
    one, beside = (own, history) if own else (history, [])
    apart = sum(turn.spend for turn in beside if turn.side)
    for ceiling in report.replayed:
        spent, count = replay(one, ceiling)
        report.replayed[ceiling] += spent + apart
        if ceiling:
            report.compactions[ceiling] += count


def _count_turn(report: Report, turn: Turn, first: bool) -> None:
    report.spend += turn.spend
    for name, value in zip(report.kinds, (
            turn.fresh * FRESH, turn.write_5m * WRITE_5M, turn.write_1h * WRITE_1H,
            turn.read * READ, turn.out * OUT)):
        report.kinds[name] += value
    for low, high in BANDS:
        if turn.context >= low and (not high or turn.context < high):
            report.bands[(low, high)] += turn.spend
    if (not first and turn.context > SMALLEST_REWRITE
            and turn.written >= REWRITTEN_SHARE * turn.context):
        report.rewrites += 1
        report.rewritten += turn.written


def transcripts(ctx: GitContext, days: float = 0, home: Path | None = None) -> list[Path]:
    """The transcripts of this repository's sessions: its main checkout's and every tree's.

    Claude Code files a session under the directory it started in, each character that is
    not a letter or a digit written as `-`. A tree inside the checkout, and one beside it
    named `<checkout>-<topic>`, both begin with the checkout's own name, and so does a
    neighbour that only shares the prefix: its sessions are counted too.
    """
    from . import worktree

    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home or Path.home()) / ".claude")
    key = re.sub(r"[^A-Za-z0-9]", "-", worktree.main_checkout(ctx).as_posix())
    since = time.time() - days * 86400 if days else 0
    found: list[Path] = []
    try:
        projects = sorted(base.joinpath("projects").iterdir())
    except OSError:
        return []
    for project in projects:
        if project.name == key or project.name.startswith(f"{key}-"):
            found += [path for path in sorted(project.rglob("*.jsonl")) if _since(path, since)]
    return found


def _since(path: Path, since: float) -> bool:
    try:
        return path.stat().st_mtime >= since
    except OSError:
        return False


def ceiling_in_effect(ctx: GitContext, home: Path | None = None) -> tuple[int, str]:
    """The context ceiling a session started here runs under, and where it is set. (0, "")
    for none, which is the model's own: near 967,000 on a model with a million-token window.

    Read where Claude Code reads it, the nearest first: the environment, this tree's local
    and committed settings, then the founder's own. A window saved for one model with
    `/autocompact` is not read: it holds for that model alone.
    """
    spoken = _tokens(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"))
    if spoken:
        return spoken, "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
    for path in (ctx.worktree_root / ".claude" / "settings.local.json",
                 ctx.worktree_root / ".claude" / "settings.json",
                 (home or Path.home()) / ".claude" / "settings.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        found = _tokens(raw.get("autoCompactWindow")) if isinstance(raw, dict) else 0
        if found:
            return found, str(path)
    return 0, ""


def _tokens(value: object) -> int:
    """A window as a settings file or the environment spells it: `300000`, `300k`, `1M`."""
    text = str(value or "").strip().lower()
    scale = {"k": 1_000, "m": 1_000_000}.get(text[-1:], 1)
    digits = text[:-1] if scale > 1 else text
    return int(float(digits) * scale) if re.fullmatch(r"\d+(\.\d+)?", digits) else 0


def render(report: Report, ceiling: tuple[int, str]) -> str:
    """The report as `claude-bp spend` prints it."""
    if not report.requests:
        return "No requests to the model in the transcripts found for this repository."
    lines = [
        f"{report.conversations} conversation(s), a subagent's among them, "
        f"{report.requests} requests ({report.by_agents} by subagents), "
        f"{_k(report.spend)} input-token equivalents.",
        "", "Where it went:",
        *(f"  {name:<20} {_share(value, report.spend)}" for name, value in report.kinds.items()),
        "", "By how full the context was on the request:",
        *(f"  {_band(band):<20} {_share(value, report.spend)}"
          for band, value in report.bands.items()),
        "",
        f"A session's first request (the always-on context and the first message): "
        f"median {_k(_median(report.floors))}.",
        f"A conversation's largest context: median {_k(_median(report.peaks))}, "
        f"largest {_k(max(report.peaks))}.",
        f"A prefix written again mid-session (expired cache, compaction): "
        f"{report.rewrites} time(s), {_k(report.rewritten)} tokens.",
        "", "The same conversations, compacted at a ceiling "
        f"(replayed with none: {_share(report.replayed[0], report.spend)} of the spend, "
        "the check on the replay):",
        *(f"  at {limit // 1000}k: {_share(report.replayed[limit], report.spend)} of the spend, "
          f"{report.compactions[limit]} compaction(s)" for limit in CEILINGS),
        "", _ceiling_line(ceiling),
    ]
    return "\n".join(lines)


def _ceiling_line(ceiling: tuple[int, str]) -> str:
    size, where = ceiling
    if size:
        return f"Ceiling in effect here: {size // 1000}k, set in {where}."
    return ("Ceiling in effect here: none, so a session compacts near the model's window "
            "(about 967k on Fable, Sonnet 5 and Opus 4.7 onward). "
            '`"autoCompactWindow": 300000` in ~/.claude/settings.json, or in the project\'s '
            ".claude/settings.json, sets one.")


def _band(band: tuple[int, int]) -> str:
    low, high = band
    return f"{low // 1000}k-{high // 1000}k" if high else f"{low // 1000}k and over"


def _share(value: float, whole: float) -> str:
    return f"{100 * value / whole:5.1f}%" if whole else "    -"


def _k(value: float) -> str:
    return f"{value / 1000:,.0f}k"


def _median(values: list) -> float:
    return sorted(values)[len(values) // 2] if values else 0
