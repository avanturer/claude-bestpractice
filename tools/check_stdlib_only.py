#!/usr/bin/env python3
"""Fail if anything under plugin/ imports a third-party package.

Stdlib-only is a hard constraint here, not a preference. These hooks run on every tool
call in every session: a dependency tree is latency on the hot path, an extra failure
mode for a component whose whole job is to be trustworthy, and a supply-chain surface.
Frontier models hallucinate package names at 4.6-6.1%, and 43% of hallucinated names
recur deterministically — the safest dependency count for a guard is zero.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL = {"founder_os", "helpers"}


def python_files() -> list[pathlib.Path]:
    files = list((ROOT / "plugin" / "lib").rglob("*.py"))
    files += [p for p in (ROOT / "plugin" / "bin").iterdir() if p.is_file()]
    return files


def main() -> int:
    allowed = set(sys.stdlib_module_names) | LOCAL
    problems: list[str] = []

    for path in python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{path.relative_to(ROOT)}: cannot parse ({exc})")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name and name not in allowed:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: third-party import {name!r}"
                    )

    if problems:
        print("stdlib-only violated:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"lint: {len(python_files())} files, stdlib only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
