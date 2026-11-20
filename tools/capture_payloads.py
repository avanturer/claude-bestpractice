#!/usr/bin/env python3
"""Capture the shape of the hook payloads this plugin reads, from the Claude Code installed here.

Writes `tests/fixtures/hook-payloads.json`, which `tests/test_hook_payloads.py` holds the plugin's
reads against: run it after upgrading Claude Code, and a field the harness renamed fails that
test instead of a gate in a founder's session. Field names only, never their values.

It runs one headless session on a throwaway repository, with a probe plugin on every event that
session reaches: it needs the founder's login and spends a few cents. Standard library only.

`--code-mode` answers what decision 0103 left open: whether PostToolUse and a mod's `tool.call`
reach a call a Code Mode script makes. Code Mode starts in no `-p`, no cloud session and no IDE
panel, so this one runs an interactive `claude` in the terminal it is started from, and writes
`tests/fixtures/code-mode-payloads.json` with its verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "hook-payloads.json"
CODE_MODE_FIXTURE = ROOT / "tests" / "fixtures" / "code-mode-payloads.json"
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart",
          "PreCompact", "Stop")
CODE_MODE_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
PROMPT = ("Do these three things in order and nothing else. 1: run the shell command `echo probe`. "
          "2: write a file named probe.txt containing the word probe. 3: start a general-purpose "
          "subagent with the description 'probe' and the prompt 'Reply with the word done.' "
          "Then reply with the word finished.")
CODE_MODE_PROMPT = ("In one Code Mode script, run the shell command `echo probe` and return what it "
                    "printed. Do nothing else, then reply with the word finished.")
# What the probe mod's line says, in place of a hook event's name.
MODULE_EVENT = "mod:tool.call"
# Code Mode came in 2.1.323, and starts beside a mod that hooks tool calls, as the probe's does,
# from 2.1.327. Older, a session opens and no script runs, which the verdict cannot tell apart
# from a refusal.
CODE_MODE_SINCE = (2, 1, 327)

# What the probe writes for each event: the field names at the top, and for a tool call the
# names inside its input and its response, the call's id, and the names of the tools a script
# used. A path on its command line is the log: the mod passes one rather than rely on the
# environment Claude Code gives the processes a mod runs.
PROBE = r'''
import json, os, sys
event = json.load(sys.stdin)
shape = {"event": event.get("hook_event_name"), "keys": sorted(event)}
if "tool_name" in event:
    shape["tool"] = event["tool_name"]
    shape["id"] = event.get("tool_use_id")
    for part in ("tool_input", "tool_response"):
        value = event.get(part)
        if isinstance(value, dict):
            shape[part] = sorted(value)
        elif value is not None:
            shape[part] = type(value).__name__
    response = event.get("tool_response")
    used = response.get("toolsUsed") if isinstance(response, dict) else None
    if isinstance(used, list):
        shape["tools_used"] = sorted({name for name in used if isinstance(name, str)})
with open(sys.argv[1] if len(sys.argv) > 1 else os.environ["PAYLOAD_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(shape) + "\n")
'''

# A mod that says each shell call its `tool.call` hook was handed, and changes nothing.
MODULE = r'''
export const register = (on: any) => {
  on('tool.call', { tool: 'Bash' }, async ($: any, e: any, next: any) => {
    const seen = JSON.stringify({ hook_event_name: EVENT, tool_name: 'Bash' })
    await $.process.run(['python3', `${$.plugin.root}/probe.py`, LOG], { stdin: seen, timeoutMs: 5000 })
      .catch(() => null)
    return next(e)
  }).catch(async ($: any, e: any, next: any) => next(e))
}
'''


def _probe_plugin(where: Path, events: tuple = EVENTS, module_log: Path | None = None) -> Path:
    plugin = where / "probe"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "payload-probe", "version": "0.0.0"}), encoding="utf-8")
    (plugin / "probe.py").write_text(PROBE, encoding="utf-8")
    run = {"type": "command", "command": f'python3 "{plugin / "probe.py"}"'}
    hooks: dict = {"hooks": {name: [{"matcher": ".*", "hooks": [run]} if "Tool" in name
                                    else {"hooks": [run]}] for name in events}}
    if module_log is not None:
        constants = f"const LOG = {json.dumps(str(module_log))}\nconst EVENT = {json.dumps(MODULE_EVENT)}\n"
        (plugin / "hooks" / "probe.ts").write_text(constants + MODULE, encoding="utf-8")
        hooks["modules"] = ["./probe.ts"]
    (plugin / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    return plugin


def _throwaway_repo(where: Path) -> Path:
    repo = where / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("probe\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid",
                    "commit", "-qm", "probe"], cwd=repo, check=True)
    return repo


def _session(where: Path, plugin: Path, log: Path, *args: str) -> str:
    repo = where / "repo"
    env = {**os.environ, "PAYLOAD_LOG": str(log)}
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
    for shape in _shapes(log):
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


def _shapes(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def code_mode_verdict(shapes: list) -> dict:
    """What a Code Mode session's log says about the events a script's shell call reached.

    A call is a script's when it falls between a `REPL` PreToolUse and that script's end, in the
    order the log was written. A shell call made outside a script answers nothing here.
    """
    open_scripts = 0
    inner: dict = {"PreToolUse": set(), "PostToolUse": set(), "PostToolUseFailure": set(),
                   MODULE_EVENT: set()}
    tools_used: set = set()
    scripts = 0
    for shape in shapes:
        event, tool = shape.get("event"), shape.get("tool")
        if tool == "REPL":
            if event == "PreToolUse":
                open_scripts += 1
                scripts += 1
            else:
                open_scripts = max(0, open_scripts - 1)
                tools_used.update(shape.get("tools_used", []))
        elif tool == "Bash" and open_scripts > 0 and event in inner:
            inner[event].add(shape.get("id") or "")
    called, answered = inner["PreToolUse"] - {""}, inner["PostToolUse"] - {""}
    return {
        "code_mode": scripts > 0,
        "pre_tool_use": bool(inner["PreToolUse"]),
        "post_tool_use": bool(inner["PostToolUse"]),
        "post_tool_use_same_id": bool(called & answered),
        "post_tool_use_failure": bool(inner["PostToolUseFailure"]),
        "module_tool_call": bool(inner[MODULE_EVENT]),
        "tools_used": sorted(tools_used),
    }


def _version() -> str:
    try:
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout
    except OSError:
        return ""
    return version.split()[0] if version else ""


def code_mode_refusal(version: str) -> str:
    """Why the Claude Code that printed `version` cannot run the probe, or "" when it can."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        return f"No Claude Code version could be read from {version!r}: is `claude` on the PATH?"
    if tuple(int(part) for part in match.groups()) < CODE_MODE_SINCE:
        since = ".".join(str(part) for part in CODE_MODE_SINCE)
        return (f"Claude Code {match.group(0)} starts no Code Mode beside a mod that hooks tool "
                f"calls; {since} or later does. Run `claude update`, then this again.")
    return ""


def code_mode_command(plugin: Path) -> list:
    """The interactive session's command line: the prompt first, since `--allowedTools` takes
    every word after it, and on 2.1.329 took the prompt as one more tool (the founder's run)."""
    return ["claude", CODE_MODE_PROMPT, "--plugin-dir", str(plugin), "--model", "sonnet",
            "--allowedTools", "Bash(echo probe)"]


def _code_mode(where: Path, repo: Path) -> int:
    version = _version()
    refusal = code_mode_refusal(version)
    if refusal:
        print(refusal)
        return 1
    log = where / "payloads.jsonl"
    plugin = _probe_plugin(where, CODE_MODE_EVENTS, module_log=log)
    print("A Claude Code session starts here in Code Mode, with a probe plugin beside your own.\n"
          "Answer Yes when it asks whether you trust the folder: this tool made it, and it holds a\n"
          "README and nothing else. Confirm turning Code Mode on if it asks.\n"
          "When Claude has answered 'finished', type /exit.\n"
          f"If the prompt below did not arrive, paste it:\n  {CODE_MODE_PROMPT}\n", flush=True)
    env = {**os.environ, "PAYLOAD_LOG": str(log), "CLAUDE_CODE_EXPERIMENTAL_CODE_MODE": "1"}
    subprocess.run(code_mode_command(plugin), cwd=str(repo), env=env)
    verdict = code_mode_verdict(_shapes(log))
    captured = {"claude_code": version, "verdict": verdict, "events": _merged(log)}
    CODE_MODE_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    CODE_MODE_FIXTURE.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{CODE_MODE_FIXTURE.relative_to(ROOT)}: Claude Code {version}")
    print(json.dumps(verdict, sort_keys=True))
    if not verdict["code_mode"]:
        print("No script ran: the folder was not trusted, Code Mode did not start (it needs the "
              "native build, a subscription or Console login, and a model it takes), or Claude "
              "made no Code Mode call.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--code-mode", action="store_true",
                        help="run one interactive Code Mode session in this terminal instead")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="payload-capture-") as raw:
        where = Path(raw)
        repo = _throwaway_repo(where)
        if args.code_mode:
            return _code_mode(where, repo)
        plugin, log = _probe_plugin(where), where / "payloads.jsonl"
        session = _session(where, plugin, log, PROMPT)
        if session:
            _session(where, plugin, log, "--resume", session, "/compact")
        captured = {"claude_code": _version(), "events": _merged(log)}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{FIXTURE.relative_to(ROOT)}: {', '.join(sorted(captured['events']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
