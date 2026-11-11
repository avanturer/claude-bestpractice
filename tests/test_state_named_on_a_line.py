"""Enforcement state named on a shell line is refused however the line spells it.

The rules that guard the plugin's own state read the paths a command writes, and a path is only
read where it is written out. Each line in `TAKEN` went through with no decision: a pattern, a
name given to a variable, `$(…)`, a program no rule knew, git restoring a file, a read piped
into a command that acts. The last of them wrote the file the founder's `+merge` is kept in.
"""

from __future__ import annotations

import json

from test_gates import GateCase

STATE = ".claude/claude-bestpractice"
TIER_B = '"$(git rev-parse --git-common-dir)/claude-bestpractice'

TAKEN = (
    f"cp /tmp/another.json {STATE}/config.json",
    f"cp -t {STATE} /tmp/config.json",
    f"rm {STATE}/failing-*.json",
    f"rm -f {STATE}/{{failing-suite,last-green}}.json",
    f'f={STATE}/config.json; echo "{{}}" > "$f"',
    f"perl -pi -e 's/true/false/' {STATE}/config.json",
    f"dd if=/dev/null of={STATE}/config.json",
    f"find {STATE} -name 'failing-suite.json' -delete",
    f"ls {STATE}/failing-*.json | xargs rm",
    f"git checkout HEAD~1 -- {STATE}/config.json",
    f"git restore --source HEAD~1 {STATE}/config.json",
    f"sudo env X=1 truncate -s 0 {STATE}/failing-suite.json",
    f"cd {STATE} && rm failing-*.json",
    f"python3 -c \"import pathlib; pathlib.Path('{STATE}') .joinpath('config.json').write_text('{{}}')\"",
    f"echo '{{\"approve:merge\": \"yes\"}}' > {TIER_B}/switch-requests.json\"",
    f"T={TIER_B}\"; rm -rf \"$T/sessions\"",
    "D=.git; rm $D/claude-bestpractice/switch-requests.json",
    "python3 - <<'PY'\nimport pathlib\npathlib.Path('.git/claude-bestpractice/switch-requests.json').write_text('{}')\nPY",
)

LEFT_ALONE = (
    f"cp {STATE}/config.json /tmp/config-as-it-is.json",
    f"W=$(mktemp -d); cp -a {STATE}/config.json \"$W/copy/\"",
    f"cat {STATE}/config.json",
    f"cat {STATE}/config.json | jq .",
    f"ls -la {STATE}/",
    f"cd {STATE} && ls",
    f"git log --oneline -3 -- {STATE}/config.json",
    f"git diff HEAD~1 -- {STATE}/",
    f"git add {STATE}/config.json",
    f"grep -rn failing-suite {STATE} plugin/",
    f"echo 'the record is {STATE}/failing-suite.json'",
    f"sed -n 1,5p {STATE}/config.json",
    f"find {STATE} -name '*.json'",
    f"cat {TIER_B}/switch-requests.json\"",
    'make -C "$HOME/claude-bestpractice" check',
    "python3 /srv/claude-bestpractice/tools/run_tests.py",
    "git commit -m 'explain failing-suite.json'",
)


class TestStateNamedOnALine(GateCase):
    def judge(self, command: str) -> tuple[str, str]:
        proc = self.gate("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                      "tool_name": "Bash", "tool_input": {"command": command}})
        try:
            said = json.loads(proc.stdout).get("hookSpecificOutput", {})
        except json.JSONDecodeError:
            said = {}
        return said.get("permissionDecision", ""), said.get("permissionDecisionReason", "")

    def test_no_spelling_takes_it(self):
        self.start()
        for command in TAKEN:
            with self.subTest(command=command):
                decision, reason = self.judge(command)
                self.assertEqual("deny", decision, reason)
                self.assertIn("enforcement state", reason)

    def test_looking_at_it_is_not_refused(self):
        self.start()
        for command in LEFT_ALONE:
            with self.subTest(command=command):
                self.assertNotIn("enforcement state", self.judge(command)[1])

    def test_a_name_in_another_case_is_the_same_file(self):
        """A filesystem that keeps no case, macOS's and Windows's default, opens `config.json`
        for `Config.json`: written under that name it was nobody's to refuse."""
        self.start()
        proc = self.gate("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / STATE / "Config.json"), "content": "{}"}})
        said = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual("deny", said["permissionDecision"])
        self.assertIn("enforcement state", said["permissionDecisionReason"])


if __name__ == "__main__":
    import unittest

    unittest.main()
