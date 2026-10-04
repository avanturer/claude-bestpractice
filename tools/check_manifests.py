#!/usr/bin/env python3
"""Refuse a release whose manifests the Claude Code CLI on this machine rejects.

Claude Code 2.1.289 began failing `claude plugin validate` for every plugin whose name starts
`claude-`, and this plugin's did. Its changelog said nothing about it; the plugin still
installed and still loaded; the README went on promising that `validate --strict` passed,
because somebody had run it once against 2.1.281 and nothing ever ran it again. Found by
running it by hand on 2.1.289, the day after that release.

What a reserved name costs next is already on record. In 2.1.280 a marketplace name the CLI
came to reserve went straight from accepted to refused, and a marketplace already added
under one stopped loading, its plugins with it — every gate gone, and nothing said.

So `make check` asks the CLI, every time, about both manifests: the marketplace at the
repository root and the plugin it lists. With no `claude` on PATH there is nobody to ask,
and it says so instead of passing in silence.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The marketplace and the plugin, each asked about on its own. They are judged differently:
# on 2.1.289 the root run named the reserved name in the marketplace's entry, and only the
# `plugin/` run named it in `plugin.json`.
TARGETS = (".", "plugin")

# Validation reads local files. A CLI that takes longer than this is not answering.
TIMEOUT = 120


def _complaints(report: object) -> list[str]:
    """Every error and warning in `claude plugin validate --json` output, as `path: message`."""
    found: list[str] = []
    if not isinstance(report, dict):
        return found
    manifests = [report.get("manifest"), *(report.get("contents") or [])]
    for manifest in manifests:
        if not isinstance(manifest, dict):
            continue
        for issue in [*(manifest.get("errors") or []), *(manifest.get("warnings") or [])]:
            if isinstance(issue, dict):
                found.append(f"{issue.get('path') or '(manifest)'}: {issue.get('message', '')}")
    return found


def ask(claude: str, target: pathlib.Path) -> list[str]:
    """What the CLI holds against `target`, under `--strict`; empty when it accepts it."""
    try:
        proc = subprocess.run(
            [claude, "plugin", "validate", "--strict", "--json", str(target)],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [f"`claude plugin validate` did not answer within {TIMEOUT}s"]
    except OSError as exc:
        return [f"`claude plugin validate` could not run: {exc}"]
    if proc.returncode == 0:
        return []
    try:
        complaints = _complaints(json.loads(proc.stdout))
    except ValueError:
        complaints = []
    if complaints:
        return complaints
    said = (proc.stdout.strip() or proc.stderr.strip()).splitlines()
    return [said[-1] if said else f"`claude plugin validate` exited {proc.returncode}"]


def main() -> int:
    claude = shutil.which("claude")
    if not claude:
        print("manifests: no `claude` on PATH, so no CLI was asked — not checked")
        return 0

    try:
        version = subprocess.run(
            [claude, "--version"], capture_output=True, text=True, timeout=30,
        ).stdout.strip() or "an unknown version"
    except (OSError, subprocess.SubprocessError):
        version = "an unknown version"

    failed = False
    for rel in TARGETS:
        complaints = ask(claude, ROOT / rel)
        for complaint in complaints:
            print(f"manifests: {rel}: {complaint}", file=sys.stderr)
        failed = failed or bool(complaints)

    if failed:
        print(f"manifests: claude {version} rejects this plugin's manifests (above).",
              file=sys.stderr)
        return 1
    print(f"manifests: claude {version} accepts both, under --strict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
