#!/usr/bin/env python3
"""Capture the shape of the hook payloads this plugin reads, from the Claude Code installed here.

Writes `tests/fixtures/hook-payloads.json`, which `tests/test_hook_payloads.py` holds the plugin's
reads against: run it after upgrading Claude Code, and a field the harness renamed fails that
test instead of a gate in a founder's session. Field names only, never their values.

It runs one headless session on a throwaway repository, with a probe plugin on every event that
session reaches: it needs the founder's login and spends a few cents. Standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "hook-payloads.json"
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart",
          "PreCompact", "Stop")
PROMPT = ("Do these three things in order and nothing else. 1: run the shell command `echo probe`. "
          "2: write a file named probe.txt containing the word probe. 3: start a general-purpose "
          "subagent with the description 'probe' and the prompt 'Reply with the word done.' "
          "Then reply with the word finished.")

# What the probe writes for each event: the field names at the top, and for a tool call the
# names inside its input and its response.
PROBE = r'''
import json, os, sys
event = json.load(sys.stdin)
shape = {"event": event.get("hook_event_name"), "keys": sorted(event)}
if "tool_name" in event:
    shape["tool"] = event["tool_name"]
    for part in ("tool_input", "tool_response"):
        value = event.get(part)
        if isinstance(value, dict):
            shape[part] = sorted(value)
        elif value is not None:
            shape[part] = type(value).__name__
with open(os.environ["PAYLOAD_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(shape) + "\n")
'''


def _probe_plugin(where: Path) -> Path:
    plugin = where / "probe"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "payload-probe", "version": "0.0.0"}), encoding="utf-8")
    (plugin / "probe.py").write_text(PROBE, encoding="utf-8")
    run = {"type": "command", "command": f'python3 "{plugin / "probe.py"}"'}
    hooks = {name: [{"matcher": ".*", "hooks": [run]} if "Tool" in name else {"hooks": [run]}]
             for name in EVENTS}
    (plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return plugin


def _session(where: Path, plugin: Path, log: Path, *args: str) -> str:
    repo = where / "repo"
    env = {**__import__("os").environ, "PAYLOAD_LOG": str(log)}
    # The three calls the prompt asks for and nothing else: no permission is bypassed, and any
    # other tool the session reaches for is refused, as headless Claude Code refuses it.
    proc = subprocess.run(["claude", "-p", *args, "--plugin-dir", str(plugin), "--model", "sonnet",
                           "--allowedTools", "Bash(echo probe)", "Write", "Agent",
                           "--output-format", "json"],
                          cwd=str(repo), env=env, capture_output=True, text=True, timeout=600)
    try:
        return str(json.loads(proc.stdout).get("session_id") or "")
    except ValueError:
        sys.exit(f"claude -p failed: {proc.stderr[-400:] or proc.stdout[-400:]}")


def _merged(log: Path) -> dict:
    """One entry per event, every field any capture of it carried, and per tool its parts."""
    events: dict = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        shape = json.loads(line)
        entry = events.setdefault(shape["event"], {"keys": []})
        entry["keys"] = sorted(set(entry["keys"]) | set(shape["keys"]))
        if "tool" in shape:
            tool = entry.setdefault("tools", {}).setdefault(shape["tool"], {})
            for part in ("tool_input", "tool_response"):
                if isinstance(shape.get(part), list):
                    tool[part] = sorted(set(tool.get(part, [])) | set(shape[part]))
                elif part in shape:
                    tool[part] = shape[part]
    return events


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="payload-capture-") as raw:
        where = Path(raw)
        repo = where / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        (repo / "README.md").write_text("probe\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid",
                        "commit", "-qm", "probe"], cwd=repo, check=True)
        plugin, log = _probe_plugin(where), where / "payloads.jsonl"
        session = _session(where, plugin, log, PROMPT)
        if session:
            _session(where, plugin, log, "--resume", session, "/compact")
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout
        captured = {"claude_code": version.split()[0] if version else "", "events": _merged(log)}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{FIXTURE.relative_to(ROOT)}: {', '.join(sorted(captured['events']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
