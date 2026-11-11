#!/usr/bin/env python3
"""Run this checkout's plugin over a copy of a clone and its state, before the founder upgrades.

    python3 tools/drill.py <clone> [--out DIR] [--days N]

The founder upgrades on top of what was working: a release is proved on fixtures, and the
state it lands on is a real clone's ledger, session records and logs written by every version
before it. This makes a copy of that clone (its commits shared, its branches, its plugin
state copied) and, in the copy, starts a session twice with the plugin as it stands here,
prints what the repairs changed and what `claude-bp status` then says, lists the defects the
clone recorded, and asks the two refusals a release most often gets wrong of every shell
line the clone's sessions really ran: a line naming the plugin's state, a push to the trunk.

The clone itself is read and never written. What keeps the run inside the copy:
  - the copy has no linked worktrees, so nothing the plugin removes is a real tree;
  - HOME is a directory of the copy's, so no settings of the founder's are written;
  - `psql`, `dropdb`, `docker`, `gh`, `claude`, `make` and their like are stubs that fail
    and write down what called them: a repair that would drop a database drops nothing;
  - no variable Claude Code exports reaches the hooks, so no live session is written to.
The shell lines are only parsed, by the functions the gate uses, never run.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "plugin" / "bin"
sys.path.insert(0, str(ROOT / "plugin" / "lib"))

from claude_bestpractice import defects, redact, spend, store  # noqa: E402
from claude_bestpractice.gitctx import resolve  # noqa: E402

# Programs a hook must not reach from a drill: each is a stub in front of PATH.
STUBBED = ("psql", "dropdb", "createdb", "pg_dump", "pg_restore", "docker", "gh", "claude",
           "make", "ssh", "curl", "wget", "npm", "npx")
SHOWN = 25


def git(args: list[str], cwd: Path) -> str:
    """git's answer, stripped. "" for a failure: every caller here reports what it got."""
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          errors="replace", timeout=600)
    return done.stdout.strip() if done.returncode == 0 else ""


def copy_of(clone: Path, out: Path) -> Path:
    """A clone of `clone` under `out` with its branches, its remote's refs and its plugin
    state, and no linked worktree. Objects are borrowed, never written."""
    copy = out / "copy"
    subprocess.run(["git", "clone", "-q", "--shared", str(clone), str(copy)], check=True,
                   timeout=1800)
    here = git(["symbolic-ref", "--short", "HEAD"], copy)
    git(["fetch", "-q", "origin", "+refs/remotes/origin/*:refs/remotes/origin/*"], copy)
    branches = [name for name in git(["for-each-ref", "--format=%(refname:short)",
                                      "refs/heads"], clone).splitlines() if name != here]
    if branches:
        git(["fetch", "-q", "origin", *(f"+refs/heads/{b}:refs/heads/{b}" for b in branches)],
            copy)
    remote = git(["remote", "get-url", "origin"], clone)
    if remote:
        git(["remote", "set-url", "origin", remote], copy)
    common = Path(git(["rev-parse", "--path-format=absolute", "--git-common-dir"], clone))
    for source, target in ((common / "claude-bestpractice", copy / ".git" / "claude-bestpractice"),
                           (clone / store.TIER_A_DIRNAME, copy / store.TIER_A_DIRNAME),
                           (common / "info" / "exclude", copy / ".git" / "info" / "exclude")):
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, target)
    return copy


def sealed(out: Path) -> dict:
    """The environment a drilled hook runs in: a home of its own, stubs in front of PATH,
    and nothing Claude Code or git exports."""
    stubs = out / "stubs"
    stubs.mkdir(parents=True, exist_ok=True)
    for name in STUBBED:
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{out}/blocked.log"\nexit 1\n',
                        encoding="utf-8")
        stub.chmod(0o755)
    (out / "home").mkdir(exist_ok=True)
    kept = {key: value for key, value in os.environ.items()
            if not key.startswith(("CLAUDE", "GIT_", "PG", "DATABASE"))}
    return {**kept, "HOME": str(out / "home"), "PATH": f"{stubs}{os.pathsep}{kept.get('PATH', '')}",
            "CLAUDE_BESTPRACTICE_NO_AGENTS": "1", "GIT_AUTHOR_NAME": "drill",
            "GIT_AUTHOR_EMAIL": "drill@localhost", "GIT_COMMITTER_NAME": "drill",
            "GIT_COMMITTER_EMAIL": "drill@localhost"}


def counted(root: Path) -> dict:
    """What the plugin keeps in a tree, by count: the ledger's states, checkpoints, the
    files of each tier."""
    tier_a = root / store.TIER_A_DIRNAME
    tier_b = root / ".git" / "claude-bestpractice"
    found = {f"cards {state.name}": len(list(state.glob("*.md")))
             for state in sorted(tier_a.joinpath("plan").glob("*")) if state.is_dir()}
    found["checkpoints"] = len(list(tier_a.joinpath("checkpoints").glob("*.md")))
    found["tier A files"] = sum(1 for path in tier_a.rglob("*") if path.is_file())
    found["tier B files"] = sum(1 for path in tier_b.rglob("*") if path.is_file())
    found["tier B bytes"] = sum(path.stat().st_size for path in tier_b.rglob("*")
                                if path.is_file())
    return found


def started(copy: Path, env: dict, session: str) -> list[str]:
    """One session start in the copy: how it ended, how long it took, what it said."""
    event = json.dumps({"session_id": session, "cwd": str(copy),
                        "hook_event_name": "SessionStart", "source": "startup"})
    began = time.time()
    done = subprocess.run([sys.executable, str(BIN / "session-start")], input=event,
                          cwd=str(copy), env=env, capture_output=True, text=True,
                          errors="replace", timeout=900)
    try:
        board = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        board = done.stdout
    lines = [f"exit {done.returncode} in {time.time() - began:.1f}s, "
             f"board {len(board)} chars, stderr {len(done.stderr)} chars"]
    lines += [f"  stderr: {line[:220]}" for line in done.stderr.strip().splitlines()[-12:]]
    lines += [f"  | {line[:220]}" for line in board.strip().splitlines()[:40]]
    return lines


def shell_lines(files: list[Path]) -> Counter:
    """Every command the transcripts' sessions gave the Bash tool, with how often."""
    found: Counter = Counter()
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    if '"Bash"' in line and '"tool_use"' in line:
                        found.update(_commands_on(line))
        except OSError:
            continue
    return found


def _commands_on(line: str) -> list[str]:
    try:
        content = (json.loads(line).get("message") or {}).get("content")
    except (ValueError, AttributeError):
        return []
    return [str(block["input"]["command"]) for block in content or []
            if isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == "Bash" and isinstance(block.get("input"), dict)
            and block["input"].get("command")]


def refused_anew(clone: Path, copy: Path, days: float) -> list[str]:
    """The shell lines the clone's sessions ran that this version's two parsers would refuse:
    one naming the plugin's state outside a plain read, one pushing to the trunk."""
    loader = importlib.machinery.SourceFileLoader("pre_tool", str(BIN / "pre-tool"))
    gate = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(gate)
    lines = shell_lines(spend.transcripts(resolve(clone), days))
    ctx = resolve(copy)
    tier_b = clone / ".git" / "claude-bestpractice"
    state: Counter = Counter()
    pushes: Counter = Counter()
    for command, times in lines.items():
        try:
            if gate.guarded_state_named(command, clone, tier_b):
                state[command] += times
            if gate.trunk_push(command, copy, ctx):
                pushes[command] += times
        except Exception as failed:  # noqa: BLE001 - a line the parser dies on is a finding
            state[f"PARSER FAILED ({type(failed).__name__}: {failed}) on: {command}"] += times
    said = [f"{sum(lines.values())} shell lines, {len(lines)} distinct, over the last "
            f"{days:g} day(s) of transcripts"]
    for name, hits in (("name the plugin's state outside a plain read", state),
                       ("push to the trunk", pushes)):
        said.append(f"{len(hits)} distinct line(s), run {sum(hits.values())} time(s), {name}:")
        said += [f"  {times}x  {redact.scrub(' '.join(command.split()))[:230]}"
                 for command, times in hits.most_common(SHOWN)]
    return said


def recorded(copy: Path) -> list[str]:
    """The defects the clone's sessions recorded against the plugin, newest last."""
    found = defects.load(resolve(copy))
    said = [f"{len(found)} on record, {sum(1 for row in found if not row.get('sent_at'))} unsent"]
    for row in found:
        said.append(f"  v{row.get('version', '?')} {row.get('gate', '?')} "
                    f"x{row.get('count', 1)}: {str(row.get('error', ''))[:260]}")
    return said


def section(title: str, lines: list[str]) -> None:
    print(f"\n== {title}")
    print("\n".join(lines) if lines else "  (nothing)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clone", help="the main checkout of the clone to drill over")
    parser.add_argument("--out", default="/tmp/bp-drill", help="where the copy is made")
    parser.add_argument("--days", type=float, default=7, help="days of transcripts to replay")
    args = parser.parse_args()
    clone, out = Path(args.clone).resolve(), Path(args.out).resolve()
    if out == clone or clone in out.parents or not (clone / ".git").is_dir():
        print(f"drill: {clone} is not a main checkout, or {out} is inside it")
        return 2
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    copy, env = copy_of(clone, out), sealed(out)
    before = counted(copy)
    section("first session start under this version", started(copy, env, "drill-0001"))
    section("second session start (nothing left to repair)", started(copy, env, "drill-0002"))
    after = counted(copy)
    section("state, before -> after", [f"  {key}: {before.get(key, 0)} -> {after.get(key, 0)}"
                                       for key in sorted({*before, *after})])
    section("what the starts changed in git", git(["status", "--short"], copy).splitlines()[:30])
    status = subprocess.run([sys.executable, str(BIN / "claude-bp"), "status"], cwd=str(copy),
                            env=env, capture_output=True, text=True, errors="replace",
                            timeout=900)
    section(f"claude-bp status (exit {status.returncode})",
            (status.stdout + status.stderr).strip().splitlines()[-45:])
    section("defects on record", recorded(copy))
    section("shell lines this version would refuse", refused_anew(clone, copy, args.days))
    blocked = out / "blocked.log"
    section("programs a hook reached for, blocked",
            blocked.read_text(errors="replace").splitlines()[:20] if blocked.exists() else [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
