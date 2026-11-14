#!/usr/bin/env python3
"""Copy a skill from another repository's checkout into a pack, as `tools/vendored.json` says.

    python3 tools/vendor_skill.py NAME --checkout DIR

Every copy a pack ships has an entry in `tools/vendored.json` under its name (decision 0101):
`repo` (owner/name on github.com), `path` (the skill's folder there), `pack`, `license` (an
SPDX id), `recheck_days`, `description`, and where needed `guard`, `move_sections` and `patch`.
A copy is added by writing its entry and running this on a clone of the upstream, and refreshed,
when the weekly upstream issue names it, by running this again on a newer clone. The entry is
what makes a copy reproducible: what was cut, moved or patched is written there, so a refresh
cannot quietly drop it.

The skill lands in `packs/<pack>/skills/<name>/` with its frontmatter rewritten: the name, the
entry's description (this repository's 40 words at most), the upstream's own functional keys
(`allowed-tools`, `argument-hint`, …), and the `metadata` `make skills` holds a copy to:
`reviewed` (today), `recheck-after-days`, `upstream`, `commit` (the checkout's HEAD) and
`license`. The upstream's licence text goes beside it as LICENSE.

- `guard` puts a short section first in the body: where a step of the upstream's would be wrong
  in a project this plugin serves, a store build in one that ships over the air, say.
- `move_sections` names `## ` sections, each by the start of its heading, moved whole into
  `references/<slug>.md`, the heading kept in place with a line naming the file. A SKILL.md
  stays under 5,000 tokens, the most Claude Code puts back of a skill after compaction.
- `patch` is a unified diff against the skill's folder, applied with `git apply`. A refresh it
  no longer applies to fails, which is the moment to read what the upstream changed.

A file the skill links outside its folder (`../../references/checklist.md`, kept at the
upstream's root) is brought into its `references/` and the link rewritten. Left behind: folders
for other agents (`agents/`, `evals/`), `POWER.md`, and images, each image a copied Markdown
file shows replaced by its caption: a profiler's screenshot is nothing a session acts on, and
one upstream's thirteen were 6.2 MB beside an 18 MB history every clone of this marketplace
carries. The run refuses a licence outside `skills.REDISTRIBUTABLE` and a description over 40
words, and writes nothing outside `packs/`; read `git diff` before committing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "lib"))

from claude_bestpractice import skills  # noqa: E402

MANIFEST = ROOT / "tools" / "vendored.json"
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


def captioned(text: str) -> str:
    """`text` with each image it shows replaced by the image's caption."""
    return _IMAGE.sub(lambda found: f"*Figure left out of this copy: {found.group(1)}*", text)


def caption_images(target: Path) -> None:
    """Each image a copied Markdown file shows, replaced by its caption; folders the images
    alone filled, removed."""
    for page in target.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        if captioned(text) != text:
            page.write_text(captioned(text), encoding="utf-8")
    for folder in sorted(target.rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def section_starts(lines: list[str]) -> list[tuple[int, str]]:
    """(index, heading) of each `## ` line outside a fenced block."""
    starts: list[tuple[int, str]] = []
    fence = ""
    for index, line in enumerate(lines):
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~"):
            fence = "" if fence == marker else fence or marker
        elif not fence and line.startswith("## "):
            starts.append((index, line[3:].strip()))
    return starts


def move_sections(body: str, headings: list[str], target: Path) -> str:
    """`body` with each section whose heading starts with one of `headings` moved whole into
    `references/<slug>.md`, its heading kept with a line naming the file."""
    lines = body.splitlines(keepends=True)
    starts = section_starts(lines)
    spans = []
    for wanted in headings:
        found = [n for n, (_, heading) in enumerate(starts) if heading.startswith(wanted)]
        if len(found) != 1:
            sys.exit(f"vendor: {len(found)} sections start `## {wanted}`; name one")
        n = found[0]
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        spans.append((starts[n][0], end, starts[n][1]))
    if spans:
        (target / "references").mkdir(exist_ok=True)
    for start, end, heading in sorted(spans, reverse=True):
        slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:60]
        page = target / "references" / f"{slug}.md"
        if page.exists():
            sys.exit(f"vendor: references/{slug}.md is the upstream's own; move another section")
        page.write_text(f"# {heading}\n\n{''.join(lines[start + 1:end]).strip()}\n",
                        encoding="utf-8")
        lines[start + 1:end] = [f"\nRead `references/{slug}.md` for this part.\n\n"]
    return "".join(lines)


def apply_patch(patch: Path, target: Path) -> None:
    """The entry's diff applied to the copy; a refresh it no longer fits stops here."""
    proc = subprocess.run(["git", "apply", f"--directory={target.relative_to(ROOT)}",
                           str(patch)], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode:
        sys.exit(f"vendor: {patch.name} no longer applies; read the upstream's change:\n"
                 f"{proc.stderr.strip()}")


def entry_for(name: str) -> dict:
    """The manifest's entry for `name`, refused when it lacks a field or says too much."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = entries.get(name)
    if not isinstance(entry, dict):
        sys.exit(f"vendor: no entry `{name}` in {MANIFEST.name}; write one first")
    missing = [key for key in ("repo", "path", "pack", "license", "recheck_days", "description")
               if key not in entry]
    if missing:
        sys.exit(f"vendor: the entry `{name}` lacks {', '.join(missing)}")
    if entry["license"] not in skills.REDISTRIBUTABLE:
        sys.exit(f"vendor: licence {entry['license']} does not allow a changed copy")
    words = len(entry["description"].split())
    if words > DESCRIPTION_WORDS:
        sys.exit(f"vendor: the description is {words} words, the cap is {DESCRIPTION_WORDS}")
    return entry


def vendor(name: str, checkout: Path) -> Path:
    """The copy of `name` written into its pack from `checkout`; its folder."""
    entry = entry_for(name)
    source = checkout / entry["path"]
    lines, body = frontmatter_and_body((source / "SKILL.md").read_text(encoding="utf-8"))
    licence_file = next(iter(sorted(checkout.glob("LICENSE*"))), None)
    if licence_file is None:
        sys.exit("vendor: the upstream has no LICENSE file to keep beside the copy")
    commit = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    target = ROOT / "packs" / entry["pack"] / "skills" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=left_behind)
    shutil.copyfile(licence_file, target / "LICENSE")
    caption_images(target)
    body = move_sections(bring_outside_files(captioned(body), source, target),
                         entry.get("move_sections", []), target)
    guard = entry.get("guard", "").strip()
    front = [f"name: {name}", f"description: {entry['description']}", *kept_lines(lines),
             "metadata:", f'  reviewed: "{datetime.date.today().isoformat()}"',
             f'  recheck-after-days: "{entry["recheck_days"]}"',
             f'  upstream: "github.com/{entry["repo"]}/{entry["path"].strip("/")}"',
             f'  commit: "{commit}"', f'  license: "{entry["license"]}"']
    (target / "SKILL.md").write_text(
        "---\n" + "\n".join(front) + "\n---\n\n"
        + (f"## Before following this here\n\n{guard}\n\n" if guard else "") + body,
        encoding="utf-8")
    if entry.get("patch"):
        apply_patch(ROOT / entry["patch"], target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", help="the copy's entry in tools/vendored.json")
    parser.add_argument("--checkout", type=Path, required=True,
                        help="a clone of the entry's upstream repository")
    args = parser.parse_args(argv)
    target = vendor(args.name, args.checkout)
    print(f"vendor: {args.name} -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
