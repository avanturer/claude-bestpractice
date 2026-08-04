#!/usr/bin/env python3
"""Refuse a change to the shipped tree that does not bump the version.

The version string is not a label on this project, it is the update key, and that was
found by executing the CLI rather than by reading it. `claude plugin update` compares the
installed version against the marketplace's and stops there:

    $ claude plugin update claude-bestpractice@claude-bestpractice
    claude-bestpractice is already at the latest version (1.0.0).

Twenty-one commits of fixes sat behind that line. The code was never fetched, the
founder's install was frozen at whatever `1.0.0` meant on the day they installed it, and
every reasonable thing they could do about it — run update again, restart, reinstall the
marketplace — reported success and changed nothing. There is no observable difference
between "up to date" and "permanently stranded"; that is what makes it worth a gate.

So: if anything under `plugin/` differs from the default branch, the version must differ
too. This is the whole of it. It costs one line in a manifest per merge and it is the only
thing standing between a fix and the person who needs it.

Deliberately narrow. `plugin/` is exactly the tree the marketplace copies into
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` — confirmed by installing and
listing it — so a change to the README, the tests or these tools reaches an `install.sh`
user by `git pull` and needs no bump. Widening this to the whole repository would make
every documentation typo a release, and a gate that fires on things that do not matter is
a gate that gets switched off.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = "plugin/"
VERSION_FILE = "plugin/lib/claude_bestpractice/__init__.py"
VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def _git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    return proc.returncode, proc.stdout.strip()


REMOTE_PREFIX = "refs/remotes/origin/"


def _default_branch() -> str | None:
    """Whatever `origin/HEAD` points at, never a branch name assumed to be `main`."""
    code, out = _git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if code == 0 and out.startswith(REMOTE_PREFIX):
        # Strip the prefix, never `rsplit` on the last slash: a branch name may contain
        # one — `claude/…` is this repository's own convention — and taking the last
        # segment produced a ref that does not exist, which made the gate report "not
        # fetched, NOT verified" and wave the change through. Found by its own tests.
        return out[len(REMOTE_PREFIX):]
    for candidate in ("main", "master"):
        if _git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}")[0] == 0:
            return candidate
    return None


def _version_at(ref: str) -> str | None:
    code, out = _git("show", f"{ref}:{VERSION_FILE}")
    if code != 0:
        return None
    match = VERSION_RE.search(out)
    return match.group(1) if match else None


def declared() -> str | None:
    match = VERSION_RE.search((ROOT / VERSION_FILE).read_text(encoding="utf-8"))
    return match.group(1) if match else None


def manifest_disagreement(version: str) -> str | None:
    """The CLI reads plugin.json. A bump only in the package is not a bump at all."""
    manifest = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest.get("version") == version:
        return None
    return (
        f"shipped: plugin.json says {manifest.get('version')} and the package says "
        f"{version}. The CLI reads plugin.json, so the bump would not be seen."
    )


def base_ref() -> tuple[str | None, str]:
    """The ref to compare against, or None and the reason nothing could be compared.

    Failing open matters here and so does saying so: a clone with no remote — CI on a
    shallow checkout, a founder offline — must still be able to run `make check`, but
    must never be told this was verified when nothing was.
    """
    branch = _default_branch()
    if branch is None:
        return None, "no origin/HEAD to compare against"
    base = f"origin/{branch}"
    if _git("rev-parse", "--verify", "--quiet", base)[0] != 0:
        return None, f"{base} is not fetched"
    return base, ""


def changed_under(base: str) -> list[str] | None:
    """Files under the shipped tree that differ from `base`, or None if git refused."""
    code, changed = _git("diff", "--name-only", base, "--", SHIPPED)
    if code != 0:
        return None

    # `git diff` does not see a file that has never been added, and a whole new module is
    # the most consequential thing that can appear under `plugin/`. Caught by this gate
    # missing its own first run: `upgrade.py` was untracked, so the diff reported two
    # changed files and said nothing about the new one.
    _, untracked = _git("ls-files", "--others", "--exclude-standard", "--", SHIPPED)
    return sorted({line for line in changed.splitlines() + untracked.splitlines() if line.strip()})


def refusal(files: list[str], base: str, version: str) -> str:
    listed = "\n".join(f"    {name}" for name in files[:10])
    more = f"\n    … and {len(files) - 10} more" if len(files) > 10 else ""
    return (
        f"shipped: {len(files)} file(s) under {SHIPPED} changed against {base}, and the\n"
        f"version is still {version} on both sides:\n{listed}{more}\n\n"
        "  `claude plugin update` compares version strings and fetches nothing when they\n"
        "  match, so merging this leaves every existing install permanently on the old\n"
        "  code while reporting itself up to date. Bump the version in:\n"
        f"    {VERSION_FILE}\n"
        "    plugin/.claude-plugin/plugin.json\n"
        "    .claude-plugin/marketplace.json\n"
        "    README.md, docs/README.ru.md, docs/README.zh.md  (version badge)"
    )


def main() -> int:
    version = declared()
    if version is None:
        print(f"shipped: {VERSION_FILE} has no __version__", file=sys.stderr)
        return 1

    # Repeated from `test_release.py` on purpose: a bump the CLI cannot see is exactly the
    # failure this file exists to prevent, so it is checked where the refusal is.
    disagreement = manifest_disagreement(version)
    if disagreement:
        print(disagreement, file=sys.stderr)
        return 1

    base, why = base_ref()
    if base is None:
        print(f"shipped: {why}, version bump NOT verified")
        return 0

    files = changed_under(base)
    if files is None:
        print(f"shipped: could not diff against {base}, version bump NOT verified")
        return 0
    if not files:
        print(f"shipped: no change to {SHIPPED} against {base}")
        return 0

    released = _version_at(base)
    if released is None:
        print(f"shipped: {base} has no readable version, bump NOT verified")
        return 0
    if released == version:
        print(refusal(files, base, version), file=sys.stderr)
        return 1

    print(f"shipped: {len(files)} file(s) changed, version {released} -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
