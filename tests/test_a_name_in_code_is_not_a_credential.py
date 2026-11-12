"""A name in code is not a credential, at both places that refuse one with `redact.find`.

#336, #337 and #348 were each a file a session could not write until it renamed correct code,
and then a line the commit reviewer named on the merge: a handoff note quoting llm.py:60 in
backticks, an attribute assigned from the parameter it is named after, a test's client handed
a module constant one argument per line. `test_redact` holds the readings; this holds the gates.
"""

from __future__ import annotations

# First: through `helpers` it puts the plugin's library on the path the next imports need.
from test_a_faked_green_is_named import ReviewCase
from test_pullrequest import PRCase

from claude_bestpractice import board, evidence, pullrequest

LLM = ("class LLM:\n    def __init__(self, access_token, out_of_tokens):\n"
       "        self._access_token = access_token\n"
       "        self._out_of_tokens = out_of_tokens\n")
CLIENT = ('NO_NETWORK = "offline"\n\nclient = Client(\n    api_key=NO_NETWORK,\n'
          "    transport=MockTransport(h),\n)\n")
NOTE = "## Handoff\n\nllm.py:60 sets `self.out_of_tokens = out_of_tokens` (a bool).\n"
MESSAGE = 'def check(ok):\n    if not ok:\n        raise ValueError("token: malformed")\n'
NAMES = {"assistant/llm.py": LLM, "tests/test_client.py": CLIENT, "notes/handoff.md": NOTE,
         "app/check.py": MESSAGE}
# A literal only the assignment rule catches, so the controls prove that rule still runs.
LITERAL = 'API_TOKEN = "Zr8Kq2Wm5Np7Tx3Lv9Bc"\n'


class TestTheWriteGateWritesThem(ReviewCase):
    def written(self, path: str, content: str):
        return self.gate("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / path), "content": content}})

    def test_none_of_them_is_refused_as_a_credential(self):
        for path, content in NAMES.items():
            with self.subTest(path=path):
                self.assertNotIn("credential", self.hook_reason(self.written(path, content)))

    def test_a_literal_beside_them_still_is(self):
        proc = self.written("assistant/llm.py", LLM + LITERAL)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("line 5", self.hook_reason(proc))


class TestTheReviewerDoesNotNameThem(ReviewCase):
    def test_a_commit_of_them_finds_nothing(self):
        self.start()
        for path, content in NAMES.items():
            self.write(path, content)
        self.assertEqual("", self.said())

    def test_a_literal_beside_them_is_named(self):
        self.start()
        self.write("assistant/llm.py", LLM + LITERAL)
        self.assertIn("secret at assistant/llm.py:5", self.said())


class TestAFindingFiledOverOneStopsBlockingTheMerge(PRCase):
    """What an older version's reviewer recorded against such a line is re-asked of the file at
    the merge, as every finding is (#80), and retired: no repair is owed for it."""

    def filed_against(self, content: str) -> list[str]:
        self.write("assistant/llm.py", content)
        self.commit("llm")
        evidence.record_green(self.ctx(), ["pytest"])
        board.add_open_item(
            self.ctx(), item_id="review-abcd-1",
            text="1 review finding(s): secret in assistant/llm.py",
            branch=self.ctx().branch, session_id="s1", subject_paths=["assistant/llm.py"])
        return pullrequest.blockers(self.ctx(), "main")

    def test_the_finding_over_the_name_is_retired(self):
        self.assertEqual([], self.filed_against(LLM))

    def test_a_finding_over_a_literal_still_blocks(self):
        self.assertIn("1 review finding(s): secret in assistant/llm.py",
                      self.filed_against(LLM + LITERAL))
