#!/usr/bin/env python3
"""Enforce the LLM-first documentation standard mechanically.

Three checks, all pure AST plus git, no model involved:

1. SIGNATURE DRIFT — a function whose parameter names, annotations or return type
   changed while its docstring did not. This is the one form of staleness that is
   both common and cheaply detectable, and it is invisible to the reader who most
   needs it: every frontier model tested loses 21-43 percentage points of detection
   accuracy exactly when the implementation moved and the docstring stayed plausible.

2. DERIVABLE FORMS — `Args:`, `Returns:`, `:param:`, `@param` and friends. They
   duplicate what the signature already states, cost context on every read, and rot
   on an independent schedule.

3. NARRATION — `# Step 1:`, commented-out code, unqualified TODOs.

Run against a diff by default. `--all` scans everything, which is what a first
adoption pass wants.

Remediation wording is deliberate: DELETE first, correct second. A deleted comment
costs nothing; a stale one actively misleads.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

def repo_root() -> pathlib.Path:
    """The repository being checked — never this tool's own install location.

    A gate that scans the directory it happens to live in silently checks the wrong
    tree the moment it is installed somewhere and run somewhere else, which is the
    normal case for a plugin.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return pathlib.Path(proc.stdout.strip())
    return pathlib.Path.cwd()


ROOT = repo_root()

DERIVABLE = re.compile(
    r"(?im)^\s*(?:Args?|Arguments|Parameters|Params|Returns?|Yields?|Raises)\s*:\s*$"
    r"|:param\s|:returns?:|:rtype:|@param\s|@returns?\s"
)
NARRATION = re.compile(r"(?im)^\s*#\s*(?:step\s*\d+\b|\d+\)\s|first,|then,|finally,|now\s+we\b)")
BARE_TODO = re.compile(r"(?i)#\s*(?:TODO|FIXME|XXX)\b(?!\s*\()")
COMMENTED_CODE = re.compile(
    r"(?m)^\s*#\s*(?:def |class |return |import |from \w+ import|if .*:|for .*:|while .*:)"
)


class Problem:
    def __init__(self, path: pathlib.Path, line: int, message: str) -> None:
        self.path, self.line, self.message = path, line, message

    def __str__(self) -> str:
        rel = self.path.relative_to(ROOT) if self.path.is_absolute() else self.path
        return f"{rel}:{self.line}: {self.message}"


def git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60
    )
    return proc.stdout if proc.returncode == 0 else ""


def signature_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Everything a caller can see, and nothing else.

    Defaults are excluded deliberately: changing a default is a behaviour change worth
    a docstring, but flagging it would fire on every tuning tweak and train the author
    to write a filler docstring to silence us.
    """
    args = node.args
    parts: list[str] = []
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        for arg in group:
            parts.append(f"{arg.arg}:{ast.unparse(arg.annotation) if arg.annotation else '?'}")
    for special in (args.vararg, args.kwarg):
        if special:
            parts.append(
                f"*{special.arg}:{ast.unparse(special.annotation) if special.annotation else '?'}"
            )
    ret = ast.unparse(node.returns) if node.returns else "?"
    return f"({','.join(parts)})->{ret}"


def functions(tree: ast.AST) -> dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Qualified name -> (node, docstring). Nested defs get a dotted path."""
    out: dict[str, tuple, ] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                out[name] = (child, ast.get_docstring(child) or "")
                walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return out


def check_signature_drift(path: pathlib.Path, base: str) -> list[Problem]:
    rel = path.relative_to(ROOT).as_posix() if path.is_absolute() else path.as_posix()
    old_source = git(["show", f"{base}:{rel}"])
    if not old_source:
        return []  # new file: nothing to drift from

    try:
        old = functions(ast.parse(old_source))
        new = functions(ast.parse(path.read_text(encoding="utf-8")))
    except (SyntaxError, UnicodeDecodeError):
        return []

    problems: list[Problem] = []
    for name, (node, doc) in new.items():
        if name not in old:
            continue
        old_node, old_doc = old[name]
        if not old_doc or not doc:
            continue  # no docstring to go stale
        if signature_of(old_node) == signature_of(node):
            continue
        if old_doc.strip() != doc.strip():
            continue
        problems.append(
            Problem(
                path,
                node.lineno,
                f"`{name}` changed signature but its docstring did not. "
                "Delete the docstring if it no longer earns its place, or update it.",
            )
        )
    return problems


def real_comments(text: str) -> list[tuple[int, str]]:
    """(line, text) for actual comment tokens only.

    Scanning raw lines would flag `"# TODO: ..."` inside a string literal — which it
    did, on this project's own test data. A comment checker that fires on strings
    teaches people to disable it.
    """
    import io
    import tokenize

    out: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                out.append((token.start[0], token.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return out


def check_content(path: pathlib.Path) -> list[Problem]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    problems: list[Problem] = []

    for name, (node, doc) in functions(tree).items():
        if doc and DERIVABLE.search(doc):
            problems.append(
                Problem(
                    path,
                    node.lineno,
                    f"`{name}` restates its signature (Args/Returns/:param:). "
                    "Types already say this. Delete those sections.",
                )
            )
        if name.endswith("__init__") and doc:
            problems.append(
                Problem(path, node.lineno, "`__init__` docstring: put it on the class instead.")
            )

    for line_no, comment in real_comments(text):
        if NARRATION.search(comment):
            problems.append(
                Problem(path, line_no, "narration comment — the code already says this.")
            )
        elif COMMENTED_CODE.search(comment):
            problems.append(
                Problem(path, line_no, "commented-out code — git already remembers it.")
            )
        elif BARE_TODO.search(comment):
            problems.append(
                Problem(
                    path,
                    line_no,
                    "TODO without an owner and a removal condition, e.g. TODO(perf): ... "
                    "Remove when X ships.",
                )
            )
    return problems


def target_files(base: str | None, scan_all: bool) -> list[pathlib.Path]:
    if scan_all:
        return [
            p
            for p in ROOT.rglob("*.py")
            if ".git" not in p.parts and "__pycache__" not in p.parts
        ]
    changed = git(["diff", "--name-only", base or "HEAD"]).splitlines()
    changed += git(["diff", "--name-only", "--cached"]).splitlines()
    return [ROOT / rel for rel in sorted(set(changed)) if rel.endswith(".py") and (ROOT / rel).exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-first documentation gate.")
    parser.add_argument("--since", default="HEAD", help="git ref to diff against")
    parser.add_argument("--all", action="store_true", help="scan the whole tree")
    args = parser.parse_args()

    files = target_files(args.since, args.all)
    if not files:
        print("docstrings: nothing to check")
        return 0

    problems: list[Problem] = []
    for path in files:
        problems.extend(check_content(path))
        if not args.all:
            problems.extend(check_signature_drift(path, args.since))

    if problems:
        print(f"docstrings: {len(problems)} problem(s) in {len(files)} file(s)")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"docstrings: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
