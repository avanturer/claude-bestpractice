#!/usr/bin/env python3
"""Catch the code an LLM writes that a person would not.

Every rule here targets a class that is measured, mechanically detectable, and has near
zero false positives. Anything requiring taste is deliberately absent: a linter that
cries wolf gets disabled, and a disabled linter enforces nothing.

The classes, and why each one:

* SWALLOWED EXCEPTIONS — error-masking constructs rose 47% across 623M real changes,
  the single highest-prevalence measured regression in generated code. Budget zero,
  never ratcheted.
* SPECULATIVE ABSTRACTION — a wrapper, factory or config knob with exactly one caller
  is a guess about a future that has not arrived. Generated code produces these
  constantly because "extensible" reads as good.
* BACKWARD-COMPAT SHIMS WITH NO CONSUMERS — deprecation paths and `_v2` names in a
  codebase nothing imports. This rule turns itself OFF once real consumers exist.
* DUPLICATION — cloned lines exceeded refactored lines for the first time on record in
  the same corpus.
* UNUSED PARAMETERS AND DEAD BRANCHES — the residue of an abandoned design.
* COMPLEXITY AND LENGTH — ratcheted rather than gated, so drift shows up as a numeric
  diff instead of invisible erosion.

Budgets live in a committed file that tooling can only lower. Raising one requires a
justification trailer, checked separately.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

BUDGET_FILE = ".claude/claude-bestpractice/slop-budget.json"

DEFAULT_BUDGETS = {
    "swallowed_exceptions": 0,
    "single_caller_abstractions": 0,
    "compat_shims": 0,
    "duplicate_blocks": 0,
    "unused_parameters": 0,
    "long_functions": 0,
    "complex_functions": 0,
}

MAX_FUNCTION_LINES = 60
MAX_COMPLEXITY = 10
MAX_PARAMS = 5
DUPLICATE_WINDOW = 6

# Matched against IDENTIFIERS from the AST, never against raw lines. A raw-line scan
# flags this file's own pattern definition, and a checker that fails on itself is one
# nobody trusts — the same lesson as scanning comment tokens instead of text.
_COMPAT_NAME = re.compile(r"(?i)^(?:\w+_v[0-9]|\w+_old|\w+_legacy|legacy_\w+|backwards?_compat\w*)$")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}


@dataclass
class Finding:
    kind: str
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.message}"


def repo_root() -> pathlib.Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30
    )
    return pathlib.Path(proc.stdout.strip()) if proc.returncode == 0 else pathlib.Path.cwd()


ROOT = repo_root()


def load_budgets() -> dict[str, int]:
    path = ROOT / BUDGET_FILE
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_BUDGETS)
    budgets = dict(DEFAULT_BUDGETS)
    for key, value in stored.items():
        if key in budgets and isinstance(value, int):
            budgets[key] = value
    return budgets


def save_budgets(counts: dict[str, int]) -> None:
    """Ratchet: a budget may fall to the observed count, never rise toward it.

    The FIRST run establishes the baseline from what is actually there, because a
    ratchet seeded at zero can never be satisfied by an existing codebase and gets
    disabled on day one. Every run after that may only lower it.

    Zero-budget classes are exempt from seeding: swallowed exceptions and dead
    abstractions are defects, not debt, and they are never granted an allowance.
    """
    path = ROOT / BUDGET_FILE
    first_run = not path.exists()
    current = load_budgets()

    settled: dict[str, int] = {}
    for key in DEFAULT_BUDGETS:
        observed = counts.get(key, 0)
        if first_run and DEFAULT_BUDGETS[key] == 0 and key in _RATCHETABLE:
            settled[key] = observed
        else:
            settled[key] = min(current.get(key, 0), observed)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settled, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# Structural debt, which an existing codebase legitimately carries and pays down.
# Everything not listed here is a defect class with a permanent budget of zero.
_RATCHETABLE = {"long_functions", "complex_functions", "duplicate_blocks"}


def python_files(paths: list[str] | None) -> list[pathlib.Path]:
    if paths:
        return [ROOT / p for p in paths if p.endswith(".py") and (ROOT / p).is_file()]
    return [
        p
        for p in ROOT.rglob("*.py")
        if not _SKIP_DIRS & set(p.relative_to(ROOT).parts)
    ]


def complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With, ast.Assert)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, (ast.IfExp, ast.comprehension)):
            score += 1
    return score


def analyse(path: pathlib.Path) -> tuple[list[Finding], Counter, dict[str, int], Counter]:
    """Findings, per-kind counts, symbols defined here, and call counts seen here."""
    rel = path.relative_to(ROOT).as_posix()
    findings: list[Finding] = []
    counts: Counter = Counter()
    defined: dict[str, int] = {}
    calls: Counter = Counter()

    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return findings, counts, defined, calls

    lines = text.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls[node.func.id] += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls[node.func.attr] += 1

        if isinstance(node, ast.ExceptHandler):
            body = node.body
            only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
            only_log = len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Call
            )
            if only_pass:
                findings.append(
                    Finding("swallowed_exception", rel, node.lineno,
                            "exception caught and discarded — the failure becomes invisible")
                )
                counts["swallowed_exceptions"] += 1
            elif node.type is None and not only_log:
                findings.append(
                    Finding("swallowed_exception", rel, node.lineno,
                            "bare except also catches KeyboardInterrupt and SystemExit")
                )
                counts["swallowed_exceptions"] += 1

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined[node.name] = node.lineno

            span = (node.end_lineno or node.lineno) - node.lineno
            if span > MAX_FUNCTION_LINES:
                findings.append(
                    Finding("long_function", rel, node.lineno,
                            f"{node.name} spans {span} lines (budget {MAX_FUNCTION_LINES})")
                )
                counts["long_functions"] += 1

            score = complexity(node)
            if score > MAX_COMPLEXITY:
                findings.append(
                    Finding("complex_function", rel, node.lineno,
                            f"{node.name} has cyclomatic complexity {score} (budget {MAX_COMPLEXITY})")
                )
                counts["complex_functions"] += 1

            used = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            } | {
                child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
            }
            params = [a.arg for a in node.args.args + node.args.kwonlyargs]
            for param in params:
                if param in ("self", "cls") or param.startswith("_"):
                    continue
                if param not in used:
                    findings.append(
                        Finding("unused_parameter", rel, node.lineno,
                                f"{node.name}({param}) never uses {param} — "
                                "a knob nobody turns is a guess about the future")
                    )
                    counts["unused_parameters"] += 1

    findings.extend(_compat_findings(tree, rel, counts))
    return findings, counts, defined, calls


def _compat_findings(tree: ast.AST, rel: str, counts: Counter) -> list[Finding]:
    """Compatibility scaffolding, found structurally rather than textually."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        name = ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            for decorator in node.decorator_list:
                label = getattr(decorator, "id", "") or getattr(decorator, "attr", "")
                if "deprecat" in label.lower():
                    out.append(
                        Finding("compat_shim", rel, node.lineno,
                                f"{node.name} is deprecated — delete it while nothing consumes it")
                    )
                    counts["compat_shims"] += 1
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Call):
            target = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if target == "warn" and any(
                getattr(a, "id", "") == "DeprecationWarning" for a in node.args
            ):
                out.append(
                    Finding("compat_shim", rel, node.lineno,
                            "DeprecationWarning — nothing external consumes this yet")
                )
                counts["compat_shims"] += 1

        if name and _COMPAT_NAME.match(name):
            out.append(
                Finding("compat_shim", rel, getattr(node, "lineno", 0),
                        f"{name} is a versioned or legacy alias — pick one name")
            )
            counts["compat_shims"] += 1
    return out


def find_duplicates(files: list[pathlib.Path]) -> tuple[list[Finding], int]:
    """Identical non-trivial line windows repeated across the tree."""
    seen: dict[str, tuple[str, int]] = {}
    findings: list[Finding] = []
    total = 0

    for path in files:
        try:
            raw = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        body = [
            (i + 1, line.strip())
            for i, line in enumerate(raw)
            if line.strip() and not line.strip().startswith(("#", '"""', "'''"))
        ]
        for start in range(0, max(0, len(body) - DUPLICATE_WINDOW)):
            window = body[start : start + DUPLICATE_WINDOW]
            key = "\n".join(text for _, text in window)
            if len(key) < 160:
                continue
            if key in seen:
                other_path, other_line = seen[key]
                if other_path != rel or abs(other_line - window[0][0]) > DUPLICATE_WINDOW:
                    findings.append(
                        Finding("duplicate_block", rel, window[0][0],
                                f"{DUPLICATE_WINDOW} lines identical to {other_path}:{other_line}")
                    )
                    total += 1
            else:
                seen[key] = (rel, window[0][0])
    return findings, total


def find_single_caller_abstractions(
    defined: dict[str, tuple[str, int]], calls: Counter
) -> tuple[list[Finding], int]:
    """A helper with exactly one caller is usually a guess, not a factoring."""
    findings: list[Finding] = []
    total = 0
    for name, (rel, lineno) in defined.items():
        if name.startswith("_") or name.startswith("test_") or name.startswith("cmd_"):
            continue
        if name in {"main", "__init__", "setUp", "tearDown"}:
            continue
        if not re.search(r"(?i)(wrapper|factory|helper|manager|handler|adapter|provider)$", name):
            continue
        if calls.get(name, 0) == 1:
            findings.append(
                Finding("single_caller_abstraction", rel, lineno,
                        f"{name} has exactly one caller — inline it until a second appears")
            )
            total += 1
    return findings, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect LLM code slop.")
    parser.add_argument("--all", action="store_true", help="scan the whole tree")
    parser.add_argument("--ratchet", action="store_true", help="lower budgets to the observed counts")
    parser.add_argument("--allow-compat", action="store_true",
                        help="the project has real external consumers")
    args = parser.parse_args()

    if args.all:
        files = python_files(None)
    else:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.splitlines()
        changed += subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.splitlines()
        files = python_files(sorted(set(changed)))

    # An empty file set still ratchets. Short-circuiting here would mean deleting the
    # offending code silently preserves its allowance, so the budget never reflects
    # reality and the ratchet stops being a ratchet.
    if not files and not args.ratchet:
        print("slop: nothing to check")
        return 0

    findings: list[Finding] = []
    counts: Counter = Counter()
    all_defined: dict[str, tuple[str, int]] = {}
    all_calls: Counter = Counter()

    for path in files:
        file_findings, file_counts, defined, calls = analyse(path)
        findings.extend(file_findings)
        counts.update(file_counts)
        rel = path.relative_to(ROOT).as_posix()
        for name, lineno in defined.items():
            all_defined.setdefault(name, (rel, lineno))
        all_calls.update(calls)

    dup_findings, dup_total = find_duplicates(files)
    findings.extend(dup_findings)
    counts["duplicate_blocks"] = dup_total

    abs_findings, abs_total = find_single_caller_abstractions(all_defined, all_calls)
    findings.extend(abs_findings)
    counts["single_caller_abstractions"] = abs_total

    if args.allow_compat:
        findings = [f for f in findings if f.kind != "compat_shim"]
        counts["compat_shims"] = 0

    observed = {key: counts.get(key, 0) for key in DEFAULT_BUDGETS}

    if args.ratchet:
        save_budgets(observed)
        print("slop: budgets ratcheted to " + json.dumps(observed, sort_keys=True))
        return 0

    budgets = load_budgets()
    over = {k: (observed[k], budgets[k]) for k in observed if observed[k] > budgets[k]}

    if over:
        print(f"slop: {len(findings)} finding(s) across {len(files)} file(s)")
        shown = 0
        for finding in findings:
            if finding.kind.rstrip("s") + "s" in over or f"{finding.kind}s" in over:
                print(f"  {finding}")
                shown += 1
                if shown >= 40:
                    print(f"  ... {len(findings) - shown} more")
                    break
        print("\nover budget:")
        for key, (got, cap) in sorted(over.items()):
            print(f"  {key}: {got} > {cap}")
        print("\nBudgets only move down. Fix the code, or run --ratchet with a justification.")
        return 1

    print(
        "slop: clean — "
        + ", ".join(f"{k}={observed[k]}/{budgets[k]}" for k in sorted(observed))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
