"""A ranked map of the repository, fitted to a token budget.

The problem this solves is the first turn of a fresh session, and the briefing of a
subagent: which twenty of four hundred files matter for THIS task. Grep answers "where
does this string appear"; it cannot answer "what is structurally central here", and a
subagent that greps its way to orientation spends its whole context doing it.

The algorithm is the one good idea in this space, reimplemented without dependencies:
build a graph where a file points at every file whose symbols it references, run
PageRank over it, personalise the vector toward identifiers mentioned in the query, and
binary-search the number of definitions that fits the budget.

Extraction is exact for Python via `ast` and regex-based elsewhere. Regex parsing is
wrong in the general case and right often enough for ranking, which only needs relative
importance rather than a correct parse. The map is a hint, never an authority — a wrong
line here costs a slightly worse ordering, not a wrong answer.

Cached by content hash, never by mtime: creating a worktree resets every mtime, and
this workflow does that constantly.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .discipline import parse_quietly
from .gitctx import GitContext

CACHE_FILE = "repomap-cache.json"
FACTS_FILE = "repomap-facts.json"
CHARS_PER_TOKEN = 3.5
DEFAULT_BUDGET_TOKENS = 1_000
MAX_FILES = 2_000
MAX_FILE_BYTES = 400_000

SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".cs", ".swift", ".c", ".h", ".cpp", ".hpp",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "target", ".next", ".tox", "vendor", ".mypy_cache", ".pytest_cache",
}

# Definition forms across the languages worth covering. Deliberately conservative:
# a missed definition costs ranking quality, a wrong one costs correctness.
_DEF_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:struct|trait|enum|impl)\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:final\s+)?"
               r"(?:class|interface|record|enum)\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*def\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*(?:module|class)\s+([A-Z][\w]*)", re.M),
]

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")

# Identifiers so common that linking on them would connect every file to every other.
_STOPWORDS = {
    "self", "this", "true", "false", "null", "none", "return", "import", "from",
    "class", "def", "func", "function", "const", "let", "var", "type", "interface",
    "string", "number", "int", "str", "bool", "list", "dict", "map", "set", "new",
    "for", "while", "with", "async", "await", "export", "default", "public", "private",
    "static", "void", "error", "err", "data", "value", "result", "item", "items",
    "name", "path", "file", "test", "args", "kwargs", "print", "len", "range",
}


@dataclass
class FileFacts:
    path: str
    defines: set[str] = field(default_factory=set)
    references: set[str] = field(default_factory=set)


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def extract_python(text: str) -> tuple[set[str], set[str]]:
    """Exact extraction via the AST. Falls back to regex on a syntax error."""
    try:
        tree = parse_quietly(text)
    except (SyntaxError, ValueError):
        return extract_generic(text)

    defines: set[str] = set()
    references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defines.add(node.name)
        elif isinstance(node, ast.Name):
            references.add(node.id)
        elif isinstance(node, ast.Attribute):
            references.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            references.update(node.module.split("."))
            references.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                references.update(alias.name.split("."))
    return defines, references - defines


def extract_generic(text: str) -> tuple[set[str], set[str]]:
    defines: set[str] = set()
    for pattern in _DEF_PATTERNS:
        defines.update(match.group(1) for match in pattern.finditer(text))
    references = {
        ident for ident in _IDENT.findall(text) if ident.lower() not in _STOPWORDS
    }
    return defines, references - defines


def scan(ctx: GitContext, paths: list[str] | None = None) -> list[FileFacts]:
    root = ctx.worktree_root
    listed = list(_sources(ctx)) if paths is None else paths
    candidates = [root / rel for rel in listed or [] if (root / rel).is_file()]

    facts: list[FileFacts] = []
    for path in candidates:
        text = _read(path)
        if not text:
            continue
        defines, references = (
            extract_python(text) if path.suffix == ".py" else extract_generic(text)
        )
        defines = {d for d in defines if d.lower() not in _STOPWORDS}
        if defines or references:
            facts.append(FileFacts(path.relative_to(root).as_posix(), defines, references))
    return facts


def build_graph(facts: list[FileFacts]) -> dict[str, dict[str, float]]:
    """File → files it depends on, weighted by how many symbols it borrows.

    A symbol defined in many files is a weak signal — it is probably a common name
    rather than a real dependency — so its weight is divided across its definers.
    """
    definers: dict[str, list[str]] = {}
    for fact in facts:
        for symbol in fact.defines:
            definers.setdefault(symbol, []).append(fact.path)

    graph: dict[str, dict[str, float]] = {fact.path: {} for fact in facts}
    for fact in facts:
        for symbol in fact.references:
            owners = definers.get(symbol)
            if not owners or len(owners) > 5:
                continue
            weight = 1.0 / len(owners)
            for owner in owners:
                if owner != fact.path:
                    graph[fact.path][owner] = graph[fact.path].get(owner, 0.0) + weight
    return graph


def pagerank(
    graph: dict[str, dict[str, float]],
    personalization: dict[str, float] | None = None,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """Power iteration. Dangling nodes redistribute through the personalisation vector.

    Without that redistribution a leaf file — which is most of a repository — leaks its
    rank out of the graph entirely and the scores stop summing to one.
    """
    nodes = list(graph)
    if not nodes:
        return {}

    if personalization:
        total = sum(personalization.values()) or 1.0
        base = {n: personalization.get(n, 0.0) / total for n in nodes}
        if not any(base.values()):
            base = {n: 1.0 / len(nodes) for n in nodes}
    else:
        base = {n: 1.0 / len(nodes) for n in nodes}

    rank = dict(base)
    for _ in range(iterations):
        nxt = {n: (1.0 - damping) * base[n] for n in nodes}
        dangling = 0.0
        for node in nodes:
            edges = graph[node]
            weight_sum = sum(edges.values())
            if weight_sum <= 0:
                dangling += rank[node]
                continue
            share = damping * rank[node]
            for target, weight in edges.items():
                nxt[target] += share * (weight / weight_sum)
        if dangling:
            for node in nodes:
                nxt[node] += damping * dangling * base[node]
        rank = nxt
    return rank


def personalize(facts: list[FileFacts], query: str) -> dict[str, float]:
    """Bias the ranking toward whatever the task is actually about."""
    if not query:
        return {}
    terms = {t.lower() for t in _IDENT.findall(query)} - _STOPWORDS
    if not terms:
        return {}

    weights: dict[str, float] = {}
    for fact in facts:
        score = 0.0
        lowered = fact.path.lower()
        for term in terms:
            if term in lowered:
                score += 3.0
            if any(term == d.lower() for d in fact.defines):
                score += 5.0
        if score:
            weights[fact.path] = score
    return weights


def render(facts: list[FileFacts], ranks: dict[str, float], budget_tokens: int) -> str:
    """Fit the highest-ranked files into the budget by binary search on the count."""
    by_path = {fact.path: fact for fact in facts}
    ordered = sorted(ranks, key=lambda p: ranks[p], reverse=True)
    budget_chars = int(budget_tokens * CHARS_PER_TOKEN)

    def build(count: int) -> str:
        lines = []
        for path in ordered[:count]:
            fact = by_path.get(path)
            if not fact:
                continue
            symbols = sorted(fact.defines)[:8]
            lines.append(f"{path}: {', '.join(symbols)}" if symbols else path)
        return "\n".join(lines)

    low, high, best = 0, len(ordered), ""
    while low <= high:
        mid = (low + high) // 2
        candidate = build(mid)
        if len(candidate) <= budget_chars:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _sources(ctx: GitContext) -> dict[str, str]:
    """The tracked source files and their blobs, as `git ls-files -s` lists them, up to
    `MAX_FILES`.

    Not a walk of the tree: it read every file of `node_modules` and every virtualenv before it
    could skip them, and on a founder's repository of 2,000 sources a subagent's map took
    seventeen seconds, past the fifteen its hook is given, on every spawn.
    """
    keys: dict[str, str] = {}
    for entry in _git(ctx, ["ls-files", "-s", "-z"]).split("\0"):
        head, _, name = entry.partition("\t")
        fields = head.split()
        if len(fields) >= 2:
            keys[name] = fields[1]
    # What the tree holds that the index does not say: a file changed in place, and a new one
    # nothing ignores. Keyed by size and time, which is what a session's own work changes.
    for args in (["ls-files", "-m", "-z"], ["ls-files", "-o", "--exclude-standard", "-z"]):
        for name in filter(None, _git(ctx, args).split("\0")):
            keys[name] = _as_it_stands(ctx.worktree_root / name)
    out: dict[str, str] = {}
    for name, key in keys.items():
        if Path(name).suffix in SOURCE_SUFFIXES and not SKIP_DIRS & set(name.split("/")[:-1]):
            out[name] = key
            if len(out) >= MAX_FILES:
                break
    return out


def _as_it_stands(path: Path) -> str:
    """A key for a file as the tree holds it, rather than as the index does."""
    try:
        stat = path.stat()
    except OSError:
        return "gone"
    return f"w:{stat.st_size}:{stat.st_mtime_ns}"


def _git(ctx: GitContext, args: list[str]) -> str:
    """What one git command prints in the tree, "" when it fails."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(ctx.worktree_root), capture_output=True,
                              encoding="utf-8", errors="surrogateescape", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _facts(ctx: GitContext, sources: dict[str, str]) -> list[FileFacts]:
    """`scan`'s answer, kept per blob: a commit re-reads the files it changed, and a map asked
    for each subagent's own task only ranks them again."""
    path = store.tier_b(ctx, FACTS_FILE)
    cached = store.read_json(path, default={}) or {}
    known = cached.get("blobs", {}) if isinstance(cached, dict) else {}
    missing = [name for name, blob in sources.items() if blob not in known]
    read = {fact.path: fact for fact in scan(ctx, missing)} if missing else {}
    kept: dict[str, list] = {}
    facts: list[FileFacts] = []
    for name, blob in sources.items():
        found = read.get(name)
        row = [sorted(found.defines), sorted(found.references)] if found else known.get(blob, [[], []])
        kept[blob] = row
        if row[0] or row[1]:
            facts.append(FileFacts(name, set(row[0]), set(row[1])))
    if missing:
        store.write_json(path, {"blobs": kept})
    return facts


def generate(
    ctx: GitContext, query: str = "", budget_tokens: int = DEFAULT_BUDGET_TOKENS
) -> str:
    """The map, cached by content so a fresh worktree does not force a cold rescan."""
    sources = _sources(ctx)
    content = hashlib.sha256("".join(f"{name}:{blob}\n" for name, blob in sorted(sources.items()))
                             .encode("utf-8", "surrogateescape")).hexdigest()
    key = f"{content}:{hashlib.sha256(query.encode()).hexdigest()[:12]}:{budget_tokens}"
    cache_path = store.tier_b(ctx, CACHE_FILE)
    cached = store.read_json(cache_path, default={}) or {}
    if isinstance(cached, dict) and cached.get("key") == key:
        return str(cached.get("map", ""))

    facts = _facts(ctx, sources)
    if not facts:
        return ""
    graph = build_graph(facts)
    ranks = pagerank(graph, personalize(facts, query))
    rendered = render(facts, ranks, budget_tokens)

    store.write_json(cache_path, {"key": key, "map": rendered})
    return rendered


# A ranked map earns its place only once orientation is hard: below this many sources a subagent
# can just look. What one costs, re-paid on every spawn.
TASK_MAP_MIN_FILES = 40
TASK_MAP_TOKENS = 400


def for_a_task(ctx: GitContext, task: str) -> str:
    """The files a subagent's task is about, ranked, under a heading; "" in a small repository.

    Asked where the task is known, when the subagent is spawned: `SubagentStart` is handed
    neither its prompt nor its description, so a map ranked there was the same for every task.
    """
    if len(_sources(ctx)) < TASK_MAP_MIN_FILES:
        return ""
    rendered = generate(ctx, query=task, budget_tokens=TASK_MAP_TOKENS)
    return f"## Where things are for this task\n{rendered}" if rendered else ""
