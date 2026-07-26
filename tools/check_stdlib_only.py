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
# Enough of the 3.9 standard library for this codebase; the check is "did someone add a
# dependency", not "is this a complete stdlib inventory".
_STDLIB_39 = {
    "abc", "argparse", "ast", "base64", "collections", "concurrent", "contextlib", "copy",
    "csv", "dataclasses", "datetime", "difflib", "enum", "errno", "fnmatch", "functools",
    "glob", "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect", "io",
    "itertools", "json", "logging", "math", "os", "pathlib", "pickle", "platform", "pprint",
    "queue", "random", "re", "secrets", "select", "shlex", "shutil", "signal", "socket",
    "sqlite3", "stat", "statistics", "string", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "token", "tokenize", "traceback", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "warnings", "xml", "zipfile", "zlib", "__future__",
}

LOCAL = {"founder_os", "helpers"}


def python_files() -> list[pathlib.Path]:
    files = list((ROOT / "plugin" / "lib").rglob("*.py"))
    files += [p for p in (ROOT / "plugin" / "bin").iterdir() if p.is_file()]
    return files


def main() -> int:
    # `sys.stdlib_module_names` is 3.10+, and 3.9 is this project's declared floor — so
    # the very first step of `make check` died on the oldest Python it claims to support.
    # Nothing caught it because the hosted matrix is opt-in and switched off.
    allowed = set(getattr(sys, "stdlib_module_names", ())) | _STDLIB_39 | LOCAL
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
