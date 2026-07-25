#!/usr/bin/env python3
"""Fail if the plugin's always-on context cost exceeds its budget.

Always-on cost is measurable to the word: roughly 3.06 tokens per description word
plus ~15 per component. Over the cap, the plugin starts appearing by name in the
founder's own usage breakdown — where its cost is itemised and its benefit, being
counterfactual, is invisible. That is a losing trade no amount of value survives.

Caps enforced here:
  - total always-on            <= 400 tokens
  - per-skill description      <= 40 words
  - per-skill body             <= 5,000 tokens (the post-compaction re-injection cap)
  - hook entries               <= 12
  - always-on knowledge layer  <= 10,400 bytes across at most 4 files
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"

TOKENS_PER_WORD = 3.06
TOKENS_PER_COMPONENT = 15
ALWAYS_ON_CAP = 400
DESCRIPTION_WORD_CAP = 40
SKILL_BODY_TOKEN_CAP = 5_000
HOOK_ENTRY_CAP = 12
KNOWLEDGE_BYTE_CAP = 10_400
KNOWLEDGE_FILE_CAP = 4

# Deliberately crude and deliberately pessimistic: a real tokeniser is a dependency,
# and over-estimating the cost of our own footprint fails in the safe direction.
CHARS_PER_TOKEN = 3.5


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    out: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def strip_comments(raw: str) -> str:
    """Our JSON configs carry `$comment` keys; they are documentation, not payload."""
    return re.sub(r'"\$comment[^"]*"\s*:\s*(\[[^\]]*\]|"[^"]*"),?', "", raw, flags=re.S)


def main() -> int:
    problems: list[str] = []
    always_on = 0.0
    components = 0

    for skill in sorted(PLUGIN.rglob("SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        meta = frontmatter(text)
        rel = skill.relative_to(ROOT)

        description = meta.get("description", "")
        words = len(description.split())
        if words > DESCRIPTION_WORD_CAP:
            problems.append(f"{rel}: description is {words} words, cap is {DESCRIPTION_WORD_CAP}")
        always_on += words * TOKENS_PER_WORD + TOKENS_PER_COMPONENT
        components += 1

        body_tokens = len(text) / CHARS_PER_TOKEN
        if body_tokens > SKILL_BODY_TOKEN_CAP:
            problems.append(
                f"{rel}: body is ~{body_tokens:.0f} tokens, cap is {SKILL_BODY_TOKEN_CAP}"
            )

    for agent in sorted((PLUGIN / "agents").glob("*.md")) if (PLUGIN / "agents").is_dir() else []:
        meta = frontmatter(agent.read_text(encoding="utf-8"))
        words = len(meta.get("description", "").split())
        if words > DESCRIPTION_WORD_CAP:
            problems.append(f"{agent.relative_to(ROOT)}: description is {words} words")
        always_on += words * TOKENS_PER_WORD + TOKENS_PER_COMPONENT
        components += 1

    hooks_file = PLUGIN / "hooks" / "hooks.json"
    entries = 0
    if hooks_file.exists():
        try:
            data = json.loads(strip_comments(hooks_file.read_text(encoding="utf-8")))
            for matchers in (data.get("hooks") or {}).values():
                for matcher in matchers:
                    entries += len(matcher.get("hooks", []))
        except json.JSONDecodeError as exc:
            problems.append(f"hooks.json is not valid JSON: {exc}")
    if entries > HOOK_ENTRY_CAP:
        problems.append(f"hooks.json: {entries} entries, cap is {HOOK_ENTRY_CAP}")

    knowledge_dir = ROOT / ".claude" / "rules"
    if knowledge_dir.is_dir():
        files = sorted(knowledge_dir.glob("*.md"))
        total = sum(f.stat().st_size for f in files)
        if len(files) > KNOWLEDGE_FILE_CAP:
            problems.append(f"knowledge layer has {len(files)} files, cap is {KNOWLEDGE_FILE_CAP}")
        if total > KNOWLEDGE_BYTE_CAP:
            problems.append(f"knowledge layer is {total} bytes, cap is {KNOWLEDGE_BYTE_CAP}")

    if always_on > ALWAYS_ON_CAP:
        problems.append(f"always-on is ~{always_on:.0f} tokens, cap is {ALWAYS_ON_CAP}")

    if problems:
        print("budget exceeded:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(
        f"budget: ~{always_on:.0f}/{ALWAYS_ON_CAP} always-on tokens across {components} "
        f"component(s), {entries}/{HOOK_ENTRY_CAP} hook entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
