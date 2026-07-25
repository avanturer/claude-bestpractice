"""Bootstrap the knowledge layer from the code that is already there.

The failure mode this exists to prevent: a plugin that requires the founder to fill in
templates before it does anything useful. Roughly half of repositories that adopt
decision records stop under five entries, and the ones that stop are the ones that
started with a blank page. The same applies to every knowledge file.

So onboarding produces a layer that is already mostly right, derived from the
repository, with every anchor verified before it is written. What it cannot derive —
the product, its users, its non-goals — is left as an explicit question rather than
invented, because a fabricated product description is worse than an absent one: the
agent will believe it.

Entities are chosen by structural centrality, not by name matching. The most-referenced
types in a codebase are the ones an agent must not get wrong, and they are exactly what
PageRank over the symbol graph surfaces.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import knowledge, repomap
from .gitctx import GitContext

MAX_ENTITIES = 10
MIN_ENTITY_REFERENCES = 2

# A type-shaped name: PascalCase, or snake_case ending in a domain-ish noun. Function
# names are excluded deliberately — an entity is a thing the product has, not an action.
_TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")

_STACK_MARKERS: list[tuple[str, str]] = [
    ("package.json", "node"),
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
]

_FRAMEWORKS = [
    ("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"),
    ("django", "Django"), ("flask", "Flask"), ("fastapi", "FastAPI"),
    ("express", "Express"), ("nestjs", "NestJS"), ("rails", "Rails"),
    ("axum", "Axum"), ("actix", "Actix"), ("gin-gonic", "Gin"),
]


@dataclass
class Findings:
    stack: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    entities: list[tuple[str, str]] = field(default_factory=list)
    file_count: int = 0
    has_tests: bool = False


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def detect(ctx: GitContext) -> Findings:
    root = ctx.worktree_root
    found = Findings()

    for marker, name in _STACK_MARKERS:
        if (root / marker).exists() and name not in found.stack:
            found.stack.append(name)

    manifest = "\n".join(
        _read(root / marker).lower() for marker, _ in _STACK_MARKERS if (root / marker).exists()
    )
    for needle, label in _FRAMEWORKS:
        if needle in manifest:
            found.frameworks.append(label)

    facts = repomap.scan(ctx)
    found.file_count = len(facts)
    found.has_tests = any(
        "test" in fact.path.lower() or "spec" in fact.path.lower() for fact in facts
    )

    found.entities = rank_entities(facts)
    return found


def rank_entities(facts: list) -> list[tuple[str, str]]:
    """The types an agent must not misunderstand, by structural centrality.

    Score is the file's PageRank times how often the name is referenced elsewhere.
    Naming heuristics would pick whatever sounds domain-ish; this picks what the rest of
    the codebase actually depends on, which is the thing that breaks when it is wrong.
    """
    if not facts:
        return []

    ranks = repomap.pagerank(repomap.build_graph(facts))
    by_path = {fact.path: fact for fact in facts}

    references: dict[str, int] = {}
    for fact in facts:
        for name in fact.references:
            references[name] = references.get(name, 0) + 1

    candidates: list[tuple[float, str, str]] = []
    for path, rank in ranks.items():
        fact = by_path.get(path)
        if not fact:
            continue
        for name in sorted(fact.defines):
            hits = references.get(name, 0)
            if _TYPE_NAME.match(name) and len(name) >= 3 and hits >= MIN_ENTITY_REFERENCES:
                candidates.append((rank * hits, name, path))

    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _score, name, path in sorted(candidates, reverse=True):
        if name not in seen:
            seen.add(name)
            picked.append((name, path))
        if len(picked) >= MAX_ENTITIES:
            break
    return picked


def render_entities(ctx: GitContext, found: Findings) -> str:
    """Only anchors that resolve are written. An unverified anchor is a future lie."""
    if not found.entities:
        return knowledge.load(ctx).entities_raw or _ENTITIES_EMPTY

    lines = [
        "# Derived from the code by `founder-os-onboard`, then edited by hand.",
        "# The `code:` anchor is checked on every `make knowledge` — a rename fails",
        "# validation rather than leaving this file describing something that is gone.",
        "",
    ]
    for name, path in found.entities:
        anchor = f"{name} @ {path}"
        if not knowledge.anchor_resolves(ctx, anchor):
            continue
        lines += [
            f"{name}:",
            f"  what: <one line — what this is in the product, not in the code>",
            f"  code: {anchor}",
            f"  invariants: <what must always hold>",
            f"  depends_on: <other entities, or none>",
            f"  breaks_if_wrong: <what visibly breaks when this is misunderstood>",
        ]
    return "\n".join(lines) + "\n"


_ENTITIES_EMPTY = (
    "# No types were central enough to derive automatically. Add 3-12 by hand:\n"
    "# Name:\n"
    "#   what: ...\n"
    "#   code: Name @ path/to/file.py\n"
    "#   invariants: ...\n"
    "#   depends_on: ...\n"
    "#   breaks_if_wrong: ...\n"
)


def render_product(found: Findings) -> str:
    """Facts where they are derivable, explicit questions where they are not.

    The stack goes in a comment rather than a section: it is derivable from the
    repository, and the harness's own doctor trims exactly that class of content.
    """
    stack = ", ".join(found.frameworks or found.stack) or "unknown"
    return "\n".join(
        [
            "# Product",
            "",
            f"<!-- detected: {stack}, {found.file_count} source files, "
            f"tests {'present' if found.has_tests else 'absent'} -->",
            "",
            "## What this is",
            "<ANSWER THIS. One or two sentences: the thing a user gets, not the tech.>",
            "",
            "## Who it is for",
            "<ANSWER THIS. The specific person. 'developers' is not an answer.>",
            "",
            "## Non-goals",
            "- <ANSWER THIS — something plausible this deliberately will not do>",
            "- <a second one>",
            "- <a third one>",
            "",
            "## Hard constraints",
            "- <legal, business or contractual limits that outrank technical preference>",
            "",
            "## Current priority",
            "<exactly one. If there are two, one of them is not the priority.>",
            "",
        ]
    )


def render_glossary(found: Findings) -> str:
    lines = [
        "# Glossary",
        "",
        "Definitions only. One line each: meaning, canonical identifier, banned synonyms.",
        "",
    ]
    for name, path in found.entities[:10]:
        lines.append(f"{name} — <definition>. Code: `{name}`. Not: <banned synonyms>.")
    return "\n".join(lines) + "\n"


def write(ctx: GitContext, force: bool = False) -> list[str]:
    """Write what is missing. Never overwrites a file the founder has already answered."""
    root = ctx.worktree_root
    found = detect(ctx)
    written: list[str] = []

    targets = [
        (root / knowledge.RULES_DIR / knowledge.PRODUCT, render_product(found)),
        (root / knowledge.DOMAIN_DIR / knowledge.ENTITIES, render_entities(ctx, found)),
        (root / knowledge.RULES_DIR / knowledge.GLOSSARY, render_glossary(found)),
    ]
    for path, content in targets:
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path.relative_to(root).as_posix())

    index = root / knowledge.RULES_DIR / knowledge.INDEX
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(knowledge.build_index(ctx), encoding="utf-8")

    (root / knowledge.DECISIONS_DIR).mkdir(parents=True, exist_ok=True)
    return written


def unanswered(ctx: GitContext) -> list[str]:
    """Placeholders still waiting on a human. Surfaced, never invented."""
    root = ctx.worktree_root
    out: list[str] = []
    for rel in (
        f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}",
        f"{knowledge.DOMAIN_DIR}/{knowledge.ENTITIES}",
        f"{knowledge.RULES_DIR}/{knowledge.GLOSSARY}",
    ):
        text = _read(root / rel)
        count = text.count("<ANSWER THIS") + len(re.findall(r"<[a-z][^>\n]{5,}>", text))
        if count:
            out.append(f"{rel}: {count} placeholder(s)")
    return out


def state_json(ctx: GitContext) -> str:
    found = detect(ctx)
    return json.dumps(
        {
            "stack": found.stack,
            "frameworks": found.frameworks,
            "files": found.file_count,
            "has_tests": found.has_tests,
            "entities": [{"name": n, "path": p} for n, p in found.entities],
        },
        indent=2,
    )
