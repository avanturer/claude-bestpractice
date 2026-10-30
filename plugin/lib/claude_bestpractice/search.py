"""A query leaves the machine: what may go with it, and which tools carry it.

The plugin ships Parallel's and Exa's search servers (`plugin/.mcp.json`) and its hooks module
routes the harness's own WebSearch through them: Parallel first, Exa for what Parallel misses,
WebSearch itself only when neither answers (decision 0049). Whichever tool carries it, a query
is read by whoever runs the engine, so a credential in it is refused here before it leaves:
"Ничего своего в запросе" is a rule the founder had to write into a project's own instructions.
"""

from __future__ import annotations

import re
from typing import Any

from . import redact

# The harness's own search, which this plugin routes. Its page fetch asks the founder per host,
# and a process start on every one of them is a cost no other read here pays.
_HARNESS = frozenset({"WebSearch"})

# An MCP server's search and page tools, by the names Parallel and Exa give them: the plugin's
# own servers (`mcp__plugin_bestpractice_parallel__web_search`), a connector, a project's.
_MCP_TOOL = re.compile(
    r"^mcp__(?P<server>.+?)__(?:web_search(?:_advanced)?(?:_exa)?|web_fetch(?:_exa)?"
    r"|web_decisions)$")
# Whose servers those are allowed to be without asking: Parallel's and Exa's, by any name a
# server is given for them.
_VOUCHED_SERVER = re.compile(r"(?i)(?:^|[_-])(?:parallel|exa)(?:$|[_-])")


def judged(tool: str) -> bool:
    """Whether a call to `tool` sends a query or a page address out of the machine."""
    return tool in _HARNESS or _MCP_TOOL.match(tool) is not None


def vouched(tool: str) -> bool:
    """Whether `tool` is a search or page tool of Parallel's or Exa's, which runs unasked.

    The founder installed this plugin to search with these, and a permission prompt on each
    query is the plugin interrupting them with its own instruction. The harness's tools keep
    the prompts they have.
    """
    found = _MCP_TOOL.match(tool)
    return found is not None and _VOUCHED_SERVER.search(found["server"]) is not None


def secrets_in(tool_input: dict[str, Any]) -> list[str]:
    """The credential detectors that fire on what this call would send. Empty when none do."""
    return redact.find("\n".join(_strings(tool_input)))


def refusal(tool: str, found: list[str]) -> str:
    """Why a query is refused, and what to send instead."""
    return (f"claude-bestpractice: this {tool} call would send what reads as a credential "
            f"({', '.join(found)}) to a search engine outside this machine. Send the question "
            "without it: name the error, the library and the version, never a key, a token, a "
            "password or a connection string.")


def _strings(value: Any) -> list[str]:
    """Every string in a tool's input, however deep."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _strings(item)]
    return []
