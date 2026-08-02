"""The DECIDED layer: what an agent must know that the code cannot tell it.

Three properties distinguish this from every memory system surveyed.

IT IS NOT INJECTED BY US. The four always-on files live at `.claude/rules/` and
`.claude/domain/` where the harness loads them natively. Injecting them again from a
hook would pay for the same tokens twice. Our job is to keep them small, keep them
true, and hand them verbatim to subagents — which do not inherit project rules at all.

IT IS IMMUTABLE, NOT EDITED. A decision is a historical fact: it was made, and that
stays true forever. It is retired by a later record naming it in `supersedes:`, never
by rewriting history. So this layer cannot go stale the way a summary does.

ITS ANCHORS ARE CHECKED. Every entity names a canonical identifier and the file it
lives in. A rename breaks the anchor, the check fails, and the entry is repaired —
rather than quietly describing something that no longer exists. Stale context is
measurably worse than none: retrieval carrying only outdated material induced calls to
dead APIs on 15 of 17 samples, where no retrieval at all produced 0 of 17.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .gitctx import GitContext

RULES_DIR = ".claude/rules"
DOMAIN_DIR = ".claude/domain"
DECISIONS_DIR = ".claude/rules/decisions"

PRODUCT = "product.md"
GLOSSARY = "glossary.md"
INDEX = "decisions-index.md"
ENTITIES = "entities.yaml"

# Caps. Each is a line in the budget, not an aspiration.
PRODUCT_MAX_LINES = 60
GLOSSARY_MAX_LINES = 32
INDEX_MAX_LINES = 14
ENTITIES_MAX_LINES = 48
DECISION_MAX_LINES = 40
LAYER_MAX_BYTES = 10_400
GLOSSARY_MAX_LINE_CHARS = 160
ENTITY_COUNT_RANGE = (3, 12)

ENTITY_KEYS = {"what", "code", "invariants", "depends_on", "breaks_if_wrong"}

# A glossary entry that argues is a rule wearing a disguise, and it crowds out the
# definition. The one measured Worse trial in this area was caused by exactly this.
_PRESCRIPTIVE = re.compile(r"(?i)\b(should|must|always|never|prefer|avoid)\b")


@dataclass
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class Entity:
    name: str
    what: str = ""
    code: str = ""
    invariants: str = ""
    depends_on: str = ""
    breaks_if_wrong: str = ""


@dataclass
class Layer:
    product: str = ""
    glossary: str = ""
    index: str = ""
    entities: list[Entity] = field(default_factory=list)
    entities_raw: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(
            len(text.encode("utf-8"))
            for text in (self.product, self.glossary, self.index, self.entities_raw)
        )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_entities(raw: str) -> list[Entity]:
    """Parse the entity file without a YAML dependency.

    The format is deliberately a two-level mapping — `name:` then indented `key: value`
    — so a fifteen-line parser is sufficient and correct. Reaching for a YAML library
    here would put a third-party package on the hot path of every session start.
    """
    entities: list[Entity] = []
    current: Entity | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            name = line.split(":", 1)[0].strip()
            if name:
                current = Entity(name=name)
                entities.append(current)
            continue
        if current is None or ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        key = key.strip()
        if key in ENTITY_KEYS:
            setattr(current, key, value.strip())
    return entities


def load(ctx: GitContext) -> Layer:
    root = ctx.worktree_root
    entities_raw = _read(root / DOMAIN_DIR / ENTITIES)
    return Layer(
        product=_read(root / RULES_DIR / PRODUCT),
        glossary=_read(root / RULES_DIR / GLOSSARY),
        index=_read(root / RULES_DIR / INDEX),
        entities=parse_entities(entities_raw),
        entities_raw=entities_raw,
    )


def _lines(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])


def anchor_resolves(ctx: GitContext, code: str) -> bool:
    """Does the entity's canonical identifier still exist in the file it names?

    Accepts `identifier @ path/to/file.py` or a bare path. A rename breaks this, which
    is the point: the entry then gets repaired instead of silently describing a symbol
    that no longer exists.
    """
    if not code:
        return False
    identifier, _, rel = code.partition("@")
    identifier, rel = identifier.strip(), rel.strip()
    if not rel:
        rel, identifier = identifier, ""
    target = ctx.worktree_root / rel
    if not target.exists():
        return False
    if not identifier:
        return True
    # Whole-word, not substring. `Order` was satisfied by `OrderLine`, `WorkOrder` and
    # `reorder`, so renaming `Order` to `PurchaseOrder` left the anchor resolving
    # against its own replacement and the headline promise — a rename fails validation
    # — was false for exactly the renames people actually perform.
    word = re.compile(rf"\b{re.escape(identifier)}\b")
    if target.is_dir():
        return any(word.search(_read(p)) for p in target.rglob("*") if p.is_file())
    return bool(word.search(_read(target)))


def validate(ctx: GitContext) -> list[Problem]:
    layer = load(ctx)
    problems: list[Problem] = []
    root = ctx.worktree_root

    if not layer.product.strip():
        problems.append(Problem(f"{RULES_DIR}/{PRODUCT}", "missing — run `claude-bp-knowledge init`"))
    else:
        if _lines(layer.product) > PRODUCT_MAX_LINES:
            problems.append(
                Problem(f"{RULES_DIR}/{PRODUCT}", f"over {PRODUCT_MAX_LINES} lines")
            )
        if "non-goal" not in layer.product.lower():
            problems.append(
                Problem(
                    f"{RULES_DIR}/{PRODUCT}",
                    "no non-goals. What the product is NOT is the least derivable and "
                    "most useful thing in this file.",
                )
            )

    if layer.glossary.strip():
        if _lines(layer.glossary) > GLOSSARY_MAX_LINES:
            problems.append(Problem(f"{RULES_DIR}/{GLOSSARY}", f"over {GLOSSARY_MAX_LINES} lines"))
        for i, line in enumerate(layer.glossary.splitlines(), start=1):
            if len(line) > GLOSSARY_MAX_LINE_CHARS:
                problems.append(
                    Problem(f"{RULES_DIR}/{GLOSSARY}:{i}", "line over 160 chars — define, do not explain")
                )
            elif _PRESCRIPTIVE.search(line) and not line.startswith("#"):
                problems.append(
                    Problem(
                        f"{RULES_DIR}/{GLOSSARY}:{i}",
                        "prescriptive wording in a glossary — a definition is not a rule",
                    )
                )

    if layer.entities_raw.strip():
        if _lines(layer.entities_raw) > ENTITIES_MAX_LINES:
            problems.append(Problem(f"{DOMAIN_DIR}/{ENTITIES}", f"over {ENTITIES_MAX_LINES} lines"))
        low, high = ENTITY_COUNT_RANGE
        if not low <= len(layer.entities) <= high:
            problems.append(
                Problem(
                    f"{DOMAIN_DIR}/{ENTITIES}",
                    f"{len(layer.entities)} entities; keep between {low} and {high}",
                )
            )
        for entity in layer.entities:
            missing = sorted(ENTITY_KEYS - {k for k in ENTITY_KEYS if getattr(entity, k)})
            if missing:
                problems.append(
                    Problem(f"{DOMAIN_DIR}/{ENTITIES}", f"`{entity.name}` missing {', '.join(missing)}")
                )
            elif not anchor_resolves(ctx, entity.code):
                problems.append(
                    Problem(
                        f"{DOMAIN_DIR}/{ENTITIES}",
                        f"`{entity.name}` anchor `{entity.code}` no longer resolves — "
                        "the code was renamed or moved. Repair or delete the entry.",
                    )
                )

    problems.extend(validate_decisions(ctx))

    if layer.total_bytes > LAYER_MAX_BYTES:
        problems.append(
            Problem(
                "knowledge layer",
                f"{layer.total_bytes} bytes over the {LAYER_MAX_BYTES} cap — "
                "this is loaded in every session and re-paid on every subagent spawn",
            )
        )
    return problems


def decision_files(ctx: GitContext) -> list[Path]:
    directory = ctx.worktree_root / DECISIONS_DIR
    return sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md")) if directory.is_dir() else []


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    out: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def validate_decisions(ctx: GitContext) -> list[Problem]:
    problems: list[Problem] = []
    for path in decision_files(ctx):
        rel = path.relative_to(ctx.worktree_root).as_posix()
        text = _read(path)
        meta = frontmatter(text)

        if _lines(text) > DECISION_MAX_LINES:
            problems.append(Problem(rel, f"over {DECISION_MAX_LINES} lines"))

        globs = [g.strip() for g in meta.get("paths", "").strip("[]").split(",") if g.strip()]
        if not globs:
            problems.append(
                Problem(rel, "no `paths:` frontmatter — without it this loads in every session")
            )
        else:
            for glob in globs:
                if not list(ctx.worktree_root.glob(glob.strip("'\""))):
                    problems.append(
                        Problem(rel, f"`paths:` glob {glob!r} matches nothing — the code moved")
                    )

        if "## Rejected" not in text:
            problems.append(
                Problem(
                    rel,
                    "no `## Rejected` section. What was considered and dismissed is the "
                    "only genuinely non-derivable content in a repository.",
                )
            )
    return problems


def build_index(ctx: GitContext) -> str:
    lines = ["# Decisions", ""]
    superseded: set[str] = set()
    entries: list[tuple[str, str, str]] = []

    for path in decision_files(ctx):
        meta = frontmatter(_read(path))
        number = path.name.split("-", 1)[0]
        title = meta.get("title") or path.stem.split("-", 1)[-1].replace("-", " ")
        if meta.get("supersedes"):
            superseded.add(meta["supersedes"].strip())
        entries.append((number, title, path.name))

    live = [e for e in entries if e[0] not in superseded]

    # Newest first when it has to be cut. Decision files sort oldest-first, so taking a
    # prefix kept the oldest records and dropped the most recent ones — then called the
    # discarded half "older". The index therefore hid exactly the decisions most likely
    # to still be in force, which is the opposite of what it is for.
    shown, dropped = live, 0
    room = INDEX_MAX_LINES - len(lines) - 1
    if len(live) > room:
        shown, dropped = live[-room:], len(live) - room

    for number, title, filename in shown:
        lines.append(f"- [{number}] {title} — `decisions/{filename}`")
    if dropped:
        lines.append(f"- ... {dropped} older, see `decisions/`")
    return "\n".join(lines) + "\n"


PLACEHOLDER = re.compile(r"<ANSWER THIS|<[a-z][^>\n]{5,}>")


def placeholders(text: str) -> int:
    """How many slots in this text are still waiting on a human.

    Here rather than in `onboard` because both need it and `onboard` imports this, not
    the other way round. Duplicating the pattern is what the slop gate exists to refuse.
    """
    return len(PLACEHOLDER.findall(text))


def unanswered_only(text: str) -> bool:
    """True when every line that says anything is a slot rather than an answer."""
    said = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return bool(said) and all(PLACEHOLDER.search(line) for line in said)


def subagent_brief(ctx: GitContext, max_chars: int = 2_000) -> str:
    """Non-goals and entities, verbatim, for a subagent that inherits nothing.

    Verbatim is load-bearing. The one measured decomposition method that paraphrased
    facts forward scored 41% worse, and it failed precisely by collapsing cross-cutting
    constraints — which is what non-goals are.
    """
    layer = load(ctx)
    parts: list[str] = []

    for block in layer.product.split("\n#"):
        if "non-goal" not in block.lower():
            continue
        # An unanswered section is a template, and shipping a template as a briefing is
        # worse than shipping nothing: it costs the subagent tokens, tells it nothing, and
        # teaches it that this channel carries noise. Verified before this check existed —
        # a subagent's entire brief was three lines reading `<ANSWER THIS — something
        # plausible this deliberately will not do>`.
        if not unanswered_only(block):
            parts.append("#" + block.strip() if not block.startswith("#") else block.strip())
        break

    if layer.entities:
        parts.append("## Entities")
        for entity in layer.entities[:12]:
            parts.append(f"- {entity.name}: {entity.what} [{entity.code}]")
            if entity.breaks_if_wrong:
                parts.append(f"    breaks if wrong: {entity.breaks_if_wrong}")

    text = "\n".join(parts).strip()
    return text[:max_chars]
