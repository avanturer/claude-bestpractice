#!/usr/bin/env python3
"""Run the test suite across processes, for the edit loop.

NOT the gate. `make check` keeps the serial run on purpose: this suite spawns real
subprocesses against real git repositories, and running every module in one process is
what lets it catch state leaking between tests — which is the class of defect this whole
project is written about. Splitting across processes hides exactly that, so the fast run
is for iterating and the serial run is for deciding.

Measured on this repository: 173s serial, 77s across four shards, same 896 tests.

Standard library only, like everything else here — a test runner that needs installing is
one more thing between a change and knowing whether it broke something.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def weight(path: Path) -> int:
    """How much work a module is, as a count of its test methods.

    A proxy, not a measurement. Real durations would need a previous run to read, and a
    balance that depends on state from last time is one that silently degrades when the
    state is stale. Test count is wrong in the small and right in the large, which is all
    the balancing needs.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 1
    return max(1, text.count("    def test_"))


def shards(modules: list[tuple[str, int]], count: int) -> list[list[str]]:
    """Heaviest module first, each onto the lightest shard so far.

    Round-robin by filename put the two heaviest modules together and left the wall clock
    at the sum of both. This is the standard longest-processing-time heuristic, and for a
    handful of shards it lands close enough to even that the ordering stops mattering.
    """
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for name, size in sorted(modules, key=lambda pair: -pair[1]):
        lightest = loads.index(min(loads))
        buckets[lightest].append(name)
        loads[lightest] += size
    return [b for b in buckets if b]


def run(bucket: list[str]) -> subprocess.Popen:
    env = dict(os.environ)
    # `unittest <module>` does not put the tests directory on the path the way `discover
    # -t tests` does, so every shard failed to import `helpers` before this line existed.
    env["PYTHONPATH"] = os.pathsep.join([str(TESTS), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return subprocess.Popen(
        [sys.executable, "-m", "unittest", *bucket, "-q"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-j", type=int, default=min(8, os.cpu_count() or 4),
        help="shards to run at once (default: cores, capped at 8)",
    )
    args = parser.parse_args()

    modules = [(p.stem, weight(p)) for p in sorted(TESTS.glob("test_*.py"))]
    if not modules:
        print("no test modules found", file=sys.stderr)
        return 1

    started = time.time()
    running = [(bucket, run(bucket)) for bucket in shards(modules, max(1, args.j))]
    failed: list[tuple[list[str], str]] = []
    for bucket, proc in running:
        output = proc.communicate()[0] or ""
        if proc.returncode != 0:
            failed.append((bucket, output))

    elapsed = time.time() - started
    if failed:
        for bucket, output in failed:
            print(f"\n--- failing shard: {' '.join(bucket)}\n{output.rstrip()}")
        print(f"\n{len(failed)} of {len(running)} shard(s) failed in {elapsed:.0f}s", file=sys.stderr)
        # Say it here rather than let a green `make check` be the discovery. A shard that
        # passes alone and fails in the serial run is state leaking between tests, which is
        # the one thing this runner cannot see.
        print("`make test` runs the same tests in one process — the gate is that one.", file=sys.stderr)
        return 1

    print(f"{len(modules)} module(s) across {len(running)} shard(s): OK in {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
