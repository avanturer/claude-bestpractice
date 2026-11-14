#!/usr/bin/env python3
"""Fail when a skill this plugin ships cannot say when it was last checked.

A skill's dated verdicts age while the file stays the same (#365). `metadata.reviewed` is what
lets `claude-bp status` tell a stale one from a current one, so a skill without it, or with
a date nobody could have checked on, does not ship.

A skill past its recheck date is printed and does not fail the check. A check that turns red
with no change to the tree would hold a hotfix release hostage to a calendar; the date is
surfaced where the founder reads, and re-checking is the work it names.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "lib"))

from claude_bestpractice import skills  # noqa: E402


def main() -> int:
    today = datetime.date.today()
    paths = sorted((ROOT / "plugin").rglob("SKILL.md"))
    problems = [problem for path in paths for problem in skills.malformed(path, today)]
    late = skills.overdue([skill for skill in map(skills.read, paths) if skill], today)
    for skill in late:
        print(f"due: {skill.path.relative_to(ROOT)} was reviewed {skill.reviewed} and is owed "
              f"a fresh look since {skill.due()}")
    if problems:
        print("skills:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"skills: {len(paths)} dated, {len(late)} due for a fresh look")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
