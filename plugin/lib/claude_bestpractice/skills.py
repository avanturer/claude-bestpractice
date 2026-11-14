"""What a skill says about itself, and whether what it says is still current.

A skill is advice the model chooses to read, and some of it is a verdict about the outside
world: a design trend, a platform's guidance, a library's API. Such a verdict ages without
the file changing. `landing-not-slop` said glassmorphism "dates a page instantly" a year
after Apple made Liquid Glass the navigation layer of every one of its platforms, and a
session following it would have pushed an app against its platform (#365).

So a skill carries the date it was last checked (`metadata.reviewed`) and, where it rests
on outside facts, how long that check holds (`metadata.recheck-after-days`). `make check`
holds this plugin's own skills to the format; `claude-bp status` names one past its date.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

SHIPPED = Path(__file__).resolve().parent.parent.parent / "skills"

REVIEWED = "metadata.reviewed"
RECHECK = "metadata.recheck-after-days"

_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+):\s*(?P<value>.*)$")
_BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    reviewed: datetime.date | None
    recheck_after_days: int

    def due(self) -> datetime.date | None:
        """The day this skill's outside facts are owed a fresh look; None for one that rests on
        none, or that never said when it was checked."""
        if self.reviewed is None or self.recheck_after_days <= 0:
            return None
        return self.reviewed + datetime.timedelta(days=self.recheck_after_days)


def meta(text: str) -> dict[str, str]:
    """A `SKILL.md`'s frontmatter as flat strings, a key one level under a map named
    `<map>.<key>`: `metadata.reviewed`.

    The subset of YAML skills are written in, read without a YAML library (the plugin is
    standard library only): scalars, quoted or not, and one level of nesting. A list or a
    block scalar is skipped, not misread.
    """
    head, sep, _ = text[3:].partition("\n---") if text.startswith("---") else ("", "", "")
    out: dict[str, str] = {}
    parent = ""
    for found in filter(None, map(_KEY.match, head.splitlines() if sep else [])):
        parent, named, value = _entry(found, parent)
        if named:
            out[named] = value
    return out


def _entry(found: re.Match[str], parent: str) -> tuple[str, str, str]:
    """One frontmatter line: the map open after it, the name it is filed under ("" for a line
    that files nothing), and its value."""
    key, value = found["key"], _unquoted(found["value"].strip())
    if value in _BLOCK_SCALARS:
        return "", "", ""
    if not found["indent"]:
        return ("", key, value) if value else (key, "", "")
    return (parent, f"{parent}.{key}", value) if parent and value else (parent, "", "")


def _unquoted(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read(path: Path) -> Skill | None:
    """The skill whose `SKILL.md` this is; None where the file cannot be read."""
    try:
        fields = meta(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return Skill(
        name=fields.get("name") or path.parent.name,
        path=path,
        reviewed=_date(fields.get(REVIEWED, "")),
        recheck_after_days=_days(fields.get(RECHECK, "")),
    )


def _date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _days(value: str) -> int:
    return int(value) if value.isdigit() else 0


def ours() -> list[Skill]:
    """The skills this plugin ships, by directory name."""
    try:
        paths = sorted(SHIPPED.glob("*/SKILL.md"))
    except OSError:
        return []
    return [skill for skill in map(read, paths) if skill is not None]


def malformed(path: Path, today: datetime.date) -> list[str]:
    """What is wrong with how this shipped skill dates itself, one line each; empty when
    nothing is. A skill with no date cannot be told stale, and a date in the future never is."""
    try:
        fields = meta(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: cannot be read ({exc})"]
    found: list[str] = []
    reviewed = _date(fields.get(REVIEWED, ""))
    if reviewed is None:
        found.append(f"{path}: `{REVIEWED}` is missing or not YYYY-MM-DD")
    elif reviewed > today:
        found.append(f"{path}: `{REVIEWED}` {reviewed} is in the future")
    if RECHECK in fields and _days(fields[RECHECK]) <= 0:
        found.append(f"{path}: `{RECHECK}` must be a whole number of days above 0")
    return found


def overdue(skills: list[Skill], today: datetime.date) -> list[Skill]:
    """The skills whose recheck date has passed, oldest first."""
    late = [skill for skill in skills if (skill.due() or today) < today]
    return sorted(late, key=lambda skill: skill.due() or today)


def overdue_line(today: datetime.date | None = None) -> str:
    """`skills due for a fresh look: <name> (reviewed …, due …), …`, or "" when none are."""
    late = overdue(ours(), today or datetime.date.today())
    if not late:
        return ""
    listed = ", ".join(f"{skill.name} (reviewed {skill.reviewed}, due {skill.due()})"
                       for skill in late)
    return (f"skills due for a fresh look: {listed}. Their dated verdicts (marked †) are "
            "re-checked against current sources before they are trusted.")
