"""Half-done work, detected mechanically instead of hoped against.

The failure is not that the agent refuses. It is that it produces something
*shaped* like the answer: a function with the right name and signature whose body is
`pass`, a handler that raises NotImplementedError, a branch with `# TODO: handle this`.
The suite passes, because nothing calls it yet. The diff looks like progress. The founder
reads none of it, and the gap surfaces weeks later as a bug that reads like a mystery.

A stub is only ever detected in code the session actually wrote. Flagging pre-existing
ones would fire on every turn in any real codebase, and a check that always fires is a
check the agent learns to route around.

Language coverage is deliberately shallow and wide rather than deep and narrow. A regex
that catches `throw new Error("not implemented")` in five languages is worth more here
than an AST that catches everything in one, because the founder is shipping a frontend
and a backend and neither is Python.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .gitctx import GitContext

MAX_FILE_BYTES = 400_000

_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("not-implemented", re.compile(
        r"(?i)(?:raise\s+NotImplementedError|NotImplementedException"
        # Go and Rust both spell it `panic(`, with and without the macro bang.
        r"|panic!?\s*\(\s*[\"'][^\"']*(?:not\s*implement|todo|unimplemented)"
        r"|todo!\s*\(|unimplemented!\s*\("
        # JS/TS/Java/C#: any throw whose message says it is not implemented.
        r"|throw\s+new\s+\w*Error\s*\(\s*[\"'][^\"']*not\s*implement)"
    )),
    # `TODO(alice)` and `TODO[PROJ-14]` name an owner or a ticket, so they are trackable
    # and allowed. `TODO: fix this` names nobody and will never be seen again.
    ("bare-todo", re.compile(r"(?://|#|/\*)\s*(?:TODO|FIXME|XXX|HACK)\b(?!\s*[(\[])")),
    ("placeholder-return", re.compile(
        r"(?im)^\s*(?:return\s+(?:null|None|nil|\{\}|\[\]|\"\"|''|0)\s*;?\s*"
        r"(?://|#)\s*(?:TODO|placeholder|stub|for now)\b)"
    )),
    ("commented-out-block", re.compile(
        r"(?m)^(?:\s*(?://|#)\s*(?:if|for|while|def |function |class |return |const |let )\S.*\n){3,}"
    )),
]

_SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt",
    ".swift", ".c", ".h", ".cc", ".cpp", ".cs", ".php", ".scala", ".sh",
}


@dataclass
class Finding:
    path: str
    line: int
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} — {self.text[:90]}"


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def python_stubs(source: str, relpath: str) -> list[Finding]:
    """Functions whose entire body is a placeholder, via AST rather than pattern.

    Exact where it can be: a `pass`-only function is unambiguous, while a regex for the
    same thing cannot tell a stub from a deliberately empty protocol method.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            finding = _stub_finding(node, relpath)
            if finding:
                out.append(finding)
    return out


def _is_abstract(node) -> bool:
    """An abstract method is empty on purpose and says so."""
    for decorator in node.decorator_list:
        name = getattr(decorator, "id", "") or getattr(decorator, "attr", "")
        if "abstract" in str(name).lower():
            return True
    return False


def _stub_finding(node, relpath: str):
    """One function: unfinished, or not."""
    body = [n for n in node.body if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
    if not body:
        if _is_abstract(node):
            return None
        return Finding(relpath, node.lineno, "empty-function", f"{node.name}() has no body")
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return Finding(relpath, node.lineno, "stub", f"{node.name}() is `pass`")
    return None


def scan(root: Path, relpaths: list[str]) -> list[Finding]:
    """Every placeholder in the given files, cheapest checks first."""
    out: list[Finding] = []
    for rel in relpaths:
        path = root / rel
        if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
            continue
        source = _read(path)
        if not source:
            continue
        if path.suffix == ".py":
            out.extend(python_stubs(source, rel))
        for line_number, line in enumerate(source.splitlines(), 1):
            for kind, pattern in _MARKERS[:3]:
                if pattern.search(line):
                    out.append(Finding(rel, line_number, kind, line.strip()))
        block = _MARKERS[3][1].search(source)
        if block:
            line_number = source[: block.start()].count("\n") + 1
            out.append(Finding(rel, line_number, "commented-out-block", "3+ commented-out code lines"))
    return out


def introduced(ctx: GitContext, relpaths: list[str], baseline: str) -> list[Finding]:
    """Only what THIS session added. Pre-existing placeholders are not its fault.

    Subtracted by (path, kind, text) rather than by line number, because inserting a
    line above an old stub would otherwise report it as new every turn — and a check
    that cries wolf is one the agent stops reading.
    """
    now = scan(ctx.worktree_root, relpaths)
    if not baseline:
        return now

    before = set()
    for rel in relpaths:
        source = _baseline_source(ctx, baseline, rel)
        if source:
            before |= {
                (f.path, f.kind, f.text)
                for f in _scan_text(source, rel)
            }
    return [f for f in now if (f.path, f.kind, f.text) not in before]


def _scan_text(source: str, rel: str) -> list[Finding]:
    out: list[Finding] = []
    if rel.endswith(".py"):
        out.extend(python_stubs(source, rel))
    for line_number, line in enumerate(source.splitlines(), 1):
        for kind, pattern in _MARKERS[:3]:
            if pattern.search(line):
                out.append(Finding(rel, line_number, kind, line.strip()))
    return out


def _baseline_source(ctx: GitContext, baseline: str, rel: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "show", f"{baseline}:{rel}"],
        cwd=str(ctx.worktree_root), capture_output=True, text=True, timeout=30,
    )
    return proc.stdout if proc.returncode == 0 else ""


def render(findings: list[Finding], limit: int = 8) -> str:
    """The refusal text, naming files rather than lecturing about diligence."""
    if not findings:
        return ""
    shown = findings[:limit]
    more = f"\n  ... and {len(findings) - limit} more" if len(findings) > limit else ""
    return (
        "Unfinished work introduced in this turn:\n"
        + "\n".join(f"  {f}" for f in shown)
        + more
        + "\nFinish these, or delete them. A stub that compiles is not progress — it is a "
        "gap that surfaces later as a bug nobody can trace back to this turn."
    )
