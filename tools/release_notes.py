#!/usr/bin/env python3
"""The changelog section for one version, or a non-zero exit.

Exists because the release is cut by a workflow rather than by a person, and a workflow
that cannot find the notes has exactly two options: publish a release with an empty body,
or refuse. The first is worse — it looks like a release, it is what everyone reads first,
and nobody ever goes back to fix it.

Usage: release_notes.py 1.0.1
"""

from __future__ import annotations

import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(text: str, version: str) -> str | None:
    """The body under `## v<version>`, up to the next `## ` heading.

    Matched on the exact heading rather than a prefix: `## v1.0.1` must not be found by
    a search for `1.0.1` inside the prose of some other entry, and `## v1.0.10` must not
    answer a request for `1.0.1`.
    """
    wanted = f"## v{version}"
    lines = text.splitlines()

    start = None
    for index, line in enumerate(lines):
        if line.strip() == wanted:
            start = index + 1
            break
    if start is None:
        return None

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break

    body = "\n".join(lines[start:end]).strip()
    return body or None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    version = sys.argv[1].lstrip("v")
    body = section(CHANGELOG.read_text(encoding="utf-8"), version)
    if body is None:
        print(
            f"release_notes: CHANGELOG.md has no '## v{version}' section with content.\n"
            "  The release is cut from this file, so an entry has to exist before the\n"
            "  version is bumped. Refusing rather than publishing an empty release.",
            file=sys.stderr,
        )
        return 1

    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
