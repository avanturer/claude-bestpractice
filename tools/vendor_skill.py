#!/usr/bin/env python3
"""Copy a skill from another repository's checkout into a pack, as decision 0101 has it.

    python3 tools/vendor_skill.py --checkout DIR --repo OWNER/NAME --skill PATH --pack PACK \
        --description "…" [--guard "…"] [--recheck-days N] [--license SPDX]

The skill lands in `packs/<pack>/skills/<name>/` with its frontmatter rewritten: the name, the
description given (trimmed to this repository's 40 words), the upstream's own functional keys
(`allowed-tools`, `argument-hint`, …), and the `metadata` `make skills` holds a copy to:
`reviewed` (today), `recheck-after-days`, `upstream`, `commit` (the checkout's HEAD) and
`license`. The upstream's licence text goes beside it as LICENSE. `--guard` puts a short
section first in the body: what this repository's packs say before the upstream's text, where a
step of it would be wrong in a project that ships over the air, say.

A file the skill links outside its folder (`../../references/checklist.md`, a checklist kept at
the upstream's root) is brought into its `references/`, and the link rewritten to it: a copy
that left it behind sends a session to nothing, which `make skills` refuses.

What is left behind: folders for other agents (`agents/`, `evals/`), `POWER.md`, and images. A
screenshot of a profiler is nothing a session acts on, and every clone of this marketplace
would carry it forever: one upstream's thirteen were 6.2 MB beside an 18 MB history. Each image
a copied Markdown file shows is replaced by its caption, marked as left out. The run
refuses a licence outside `skills.REDISTRIBUTABLE` and a description over 40 words. It writes
nothing outside `packs/`; read `git diff` before committing, as for any change.
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "lib"))

from claude_bestpractice import skills  # noqa: E402

LEFT_BEHIND = {"agents", "evals", "POWER.md"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)\s]+\.(?:png|jpe?g|gif|webp)\)", re.IGNORECASE)
_OUTSIDE = re.compile(r"\.\./[\w./-]+\.md")
DESCRIPTION_WORDS = 40
# Keys a skill's own frontmatter may carry that change what Claude Code does with it.
FUNCTIONAL_KEYS = ("argument-hint", "arguments", "allowed-tools", "disallowed-tools", "model",
                   "effort", "context", "agent", "when_to_use", "user-invocable",
                   "disable-model-invocation", "paths", "shell")


def frontmatter_and_body(text: str) -> tuple[list[str], str]:
    """The frontmatter's lines and the body after it."""
    if not text.startswith("---"):
        return [], text
    head, _, body = text[3:].partition("\n---")
    return head.strip("\n").splitlines(), body.lstrip("\n")


def kept_lines(lines: list[str]) -> list[str]:
    """The upstream's functional keys, each with the lines nested under it."""
    kept: list[str] = []
    keeping = False
    for line in lines:
        top = re.match(r"^([A-Za-z0-9_-]+):", line)
        if top:
            keeping = top.group(1) in FUNCTIONAL_KEYS
        if keeping:
            kept.append(line)
    return kept


def bring_outside_files(body: str, source: Path, target: Path) -> str:
    """`body` with each link to a file outside the skill's folder rewritten to a copy of that
    file in its `references/`."""
    for link in sorted(set(_OUTSIDE.findall(body))):
        found = (source / link).resolve()
        if found.is_file():
            (target / "references").mkdir(exist_ok=True)
            shutil.copyfile(found, target / "references" / found.name)
            body = body.replace(link, f"references/{found.name}")
    return body


def left_behind(_folder: str, names: list[str]) -> list[str]:
    """The names in a folder of the upstream's skill that the copy does without."""
    return [name for name in names
            if name in LEFT_BEHIND or Path(name).suffix.lower() in IMAGES]


def caption_images(target: Path) -> None:
    """Each image a copied Markdown file shows, replaced by its caption; folders the images
    alone filled, removed."""
    for page in target.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        captioned = _IMAGE.sub(lambda found: f"*Figure left out of this copy: {found.group(1)}*",
                               text)
        if captioned != text:
            page.write_text(captioned, encoding="utf-8")
    for folder in sorted(target.rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="owner/name on github.com")
    parser.add_argument("--skill", required=True, help="the skill's folder inside the repository")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--guard", default="")
    parser.add_argument("--recheck-days", type=int, default=90)
    parser.add_argument("--license", default="", help="SPDX id, when the frontmatter names none")
    args = parser.parse_args()

    source = args.checkout / args.skill
    upstream_text = (source / "SKILL.md").read_text(encoding="utf-8")
    lines, body = frontmatter_and_body(upstream_text)
    fields = skills.meta(upstream_text)
    licence = args.license or fields.get("license", "")
    if licence not in skills.REDISTRIBUTABLE:
        sys.exit(f"vendor: licence {licence or 'unnamed'} does not allow a changed copy")
    words = len(args.description.split())
    if words > DESCRIPTION_WORDS:
        sys.exit(f"vendor: the description is {words} words, the cap is {DESCRIPTION_WORDS}")
    licence_file = next(iter(sorted(args.checkout.glob("LICENSE*"))), None)
    if licence_file is None:
        sys.exit("vendor: the upstream has no LICENSE file to keep beside the copy")
    commit = subprocess.run(["git", "-C", str(args.checkout), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()

    name = fields.get("name") or source.name
    target = ROOT / "packs" / args.pack / "skills" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=left_behind)
    caption_images(target)
    shutil.copyfile(licence_file, target / "LICENSE")
    body = bring_outside_files(body, source, target)
    guard = f"## Before following this here\n\n{args.guard.strip()}\n\n" if args.guard else ""
    front = [f"name: {name}", f"description: {args.description}", *kept_lines(lines),
             "metadata:", f'  reviewed: "{datetime.date.today().isoformat()}"',
             f'  recheck-after-days: "{args.recheck_days}"',
             f'  upstream: "github.com/{args.repo}/{args.skill.strip("/")}"',
             f'  commit: "{commit}"', f'  license: "{licence}"']
    (target / "SKILL.md").write_text("---\n" + "\n".join(front) + "\n---\n\n" + guard + body,
                                     encoding="utf-8")
    print(f"vendor: {args.repo}/{args.skill} at {commit[:12]} -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
