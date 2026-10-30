"""A tool an MCP server offers that writes files, judged as the write it is (decision 0060).

Serena and the reference filesystem server write files with tools of their own, and every rule
that reads a write's path saw nothing of them: no card, no lease, no protected file, no tree.
The call is judged as the harness's own `Write` of the file it names, or as `mv` for a move,
and the permission prompt its server's tools carry is left where it was.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

# Marks an event read as a write here, with the tool the server named, so nothing that
# judges it approves the call in the server's place.
SERVED_BY = "claude_bestpractice_served_by"

# By the names the servers give them: the reference filesystem server's `write_file`,
# `edit_file`, `create_directory` and `move_file`, and Serena's editing tools (v2.1).
_WRITES = re.compile(
    r"^mcp__.+?__(?P<tool>write_file|edit_file|create_directory|move_file|create_text_file"
    r"|replace_content|replace_in_files|insert_at_line|replace_symbol_body|insert_after_symbol"
    r"|insert_before_symbol|rename_symbol|delete_lines|replace_lines|safe_delete_symbol)$")
# Where they take the file: the filesystem server's `path`, Serena's `relative_path`, which is
# relative to the project the session works in.
_PATH_KEYS = ("path", "relative_path", "file_path")
_TEXT_KEYS = ("content", "body", "repl")


def as_a_write(raw: dict[str, Any]) -> dict[str, Any] | None:
    """This hook event read as the harness's own write: `Write` of the one file the call names,
    or `Bash` running `mv` for a move. None for any other call, or one that names no file."""
    found = _WRITES.match(str(raw.get("tool_name") or ""))
    given = raw.get("tool_input")
    if not found or not isinstance(given, dict):
        return None
    if found["tool"] == "move_file":
        ends = [_text(given, "source"), _text(given, "destination")]
        read = ("Bash", {"command": "mv -- " + " ".join(shlex.quote(end) for end in ends)})
    else:
        ends = [next(filter(None, (_text(given, key) for key in _PATH_KEYS)), "")]
        text = next(filter(None, (_text(given, key) for key in _TEXT_KEYS)), "")
        read = ("Write", {"file_path": ends[0], "content": text})
    if not all(ends):
        return None
    return {**raw, SERVED_BY: raw["tool_name"], "tool_name": read[0], "tool_input": read[1]}


def _text(given: dict[str, Any], key: str) -> str:
    """The string a tool's input holds under `key`, "" for anything else."""
    value = given.get(key)
    return value if isinstance(value, str) else ""
