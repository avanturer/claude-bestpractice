#!/usr/bin/env python3
"""Name each skill a pack copied from another repository whose upstream has changed since.

A pack ships copies, never links: a copy is reviewed, held to this repository's checks and
trimmed to its budget, and a link would load whatever the upstream pushed last, with the
agent's permissions (decision 0101). The cost of a copy is that it ages. Each one says where
it came from and at which commit (`metadata.upstream`, `metadata.commit`), and this asks
GitHub whether the upstream's folder has moved past that commit.

Run weekly by `.github/workflows/upstream.yml`, which files one issue for what it finds, each
line with the command that rebuilds the copy from its entry in `tools/vendored.json`. A change
upstream is read and taken in by a pull request like any other change: never merged on its own,
because a skill's text is instructions to an agent.

Exit 0: nothing moved. 1: something moved, listed. 2: GitHub could not be asked.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "lib"))

from claude_bestpractice import skills  # noqa: E402

API = "https://api.github.com"

Fetch = Callable[[str], object]


def fetch(url: str) -> object:
    """One GET of GitHub's REST API, with the workflow's token where there is one."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "claude-bestpractice-upstream-check",
    })
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def moved(owner: str, repo: str, folder: str, commit: str, get: Fetch = fetch) -> str:
    """How the upstream's `folder` moved past `commit` on its default branch: "" when it has
    not, else a line saying how and where to read the change."""
    base = f"{API}/repos/{owner}/{repo}"
    default = _field(get(base), "default_branch")
    query = urllib.parse.urlencode({"path": folder, "sha": default, "per_page": 1})
    latest = get(f"{base}/commits?{query}")
    if not isinstance(latest, list) or not latest:
        return f"`{folder}` is gone from {owner}/{repo}@{default}"
    last = _field(latest[0], "sha")
    if last == commit:
        return ""
    status = _field(get(f"{base}/compare/{commit}...{last}"), "status")
    if status in ("behind", "identical"):
        return ""
    return (f"`{folder}` changed after {commit[:12]} ({status}): "
            f"https://github.com/{owner}/{repo}/compare/{commit}...{last}")


def _field(answer: object, key: str) -> str:
    if not isinstance(answer, dict) or not isinstance(answer.get(key), str):
        raise ValueError(f"GitHub's answer has no `{key}`")
    return answer[key]


def main(get: Fetch = fetch) -> int:
    copies = [(path, source) for path in sorted(ROOT.glob("packs/*/skills/*/SKILL.md"))
              if (source := skills.upstream(path)) is not None]
    if not copies:
        print("upstream: no pack skill is a copy of another repository's")
        return 0
    changed, failed = [], []
    for path, (owner, repo, folder, commit) in copies:
        rel = path.relative_to(ROOT)
        try:
            said = moved(owner, repo, folder, commit, get)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failed.append(f"- {rel}: {owner}/{repo} could not be asked ({exc})")
            continue
        if said:
            changed.append(f"- {rel}: {said}. Refresh: `python3 tools/vendor_skill.py "
                           f"{path.parent.name} --checkout <a clone of {owner}/{repo}>`")
    for line in changed + failed:
        print(line)
    print(f"upstream: {len(copies)} cop(ies), {len(changed)} changed upstream, "
          f"{len(failed)} not asked")
    return 1 if changed else 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
