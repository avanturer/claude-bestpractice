"""Issue #293: one `+merge` ran out partway through a stack, the refusal pointed at other chats,
and a pull request GitHub closed was not covered once it was opened again.

The founder said `+merge +release` once for a stack of four pull requests, none of them open
yet: the one merge decision 0010 makes of a word said with none open. A later merge of the stack
was refused with "the founder's `+merge` on record names #1108, feat/growth-after-value, #1044,
and not this merge: it was said to …", other chats' pull requests, while the word said in this
chat had been used on its own merges and nothing said so.

Then the word named #1120 and #1121. #1120 merged with its branch deleted, so GitHub closed
#1121, whose base it was, and the same branch and commit opened again as #1124 were refused: the
word had named #1121 by the moment it was opened, and #1124 was opened later.
"""

from __future__ import annotations

import unittest

from helpers import git
from test_pullrequest import PRCase

from claude_bestpractice import evidence, pullrequest


class WordCase(PRCase):
    def setUp(self) -> None:
        super().setUp()
        # Branches git has, or the Stop gate's `reconcile` settles their pull requests as
        # closed, and another chat's word would leave nothing in the pool to point at.
        for branch in ("feat/a", "feat/b", "feat/c", "feat/s2"):
            git(["branch", branch, "main"], self.repo)

    def merge(self, number: int, session_id: str = "s1"):
        return self.ran("mcp__github__merge_pull_request",
                        {"owner": "o", "repo": "r", "pullNumber": number}, session_id)

    def refused(self, number: int, session_id: str = "s1") -> str:
        proc = self.merge(number, session_id)
        self.assertEqual("deny", self.decision(proc), "precondition: the merge is refused")
        return self.reason(proc)


class TestARefusalAfterTheWordSaysWhatItWasUsedOn(WordCase):
    def used_on_one(self) -> None:
        """Said with nothing open, then a stack opened and merged one by one, as in #293."""
        self.start()
        self.accept()
        self.open_pr(61, "feat/a")
        merged = self.merge(61)
        self.assertNotEqual("deny", self.decision(merged), self.reason(merged))
        # Merged from a tree on another branch, it is settled the way the Stop gate settles it,
        # by the trunk's history; nothing checked here was about it (#135).
        pullrequest.settle(self.ctx(), "feat/a", pullrequest.MERGED)
        self.open_pr(62, "feat/b")

    def test_it_names_the_merges_the_word_was_used_on(self):
        self.used_on_one()
        said = self.refused(62)
        self.assertIn("the founder's `+merge` in this chat was used on #61", said)
        self.assertIn("this one was opened after they said it", said)

    def test_it_does_not_point_at_a_word_said_in_another_chat(self):
        """The pool is the clone's: another chat's word named its own pull request there."""
        self.start("s2")
        self.open_pr(71, "feat/s2", session_id="s2")
        self.accept("s2")
        self.used_on_one()
        said = self.refused(62)
        self.assertIn("used on #61", said)
        self.assertNotIn("#71", said)
        self.assertNotIn("it was said to", said)

    def test_a_merge_it_was_used_on_is_not_put_on_another_chat(self):
        """`--auto` spends the word and leaves the pull request open (decision 0038). Asked
        again, it is this chat's word that was used, whatever another chat's left in the pool."""
        self.start("s2")
        self.open_pr(71, "feat/s2", session_id="s2")
        self.accept("s2")
        self.start()
        self.open_pr(61, "feat/a")
        self.accept()
        auto = self.ran("Bash", {"command": "gh pr merge 61 --auto --squash"})
        self.assertNotEqual("deny", self.decision(auto), self.reason(auto))
        said = self.refused(61)
        self.assertIn("the founder's `+merge` in this chat was used on #61, and not this merge.",
                      said)
        self.assertNotIn("it was said to", said)

    def test_a_new_word_starts_its_own_account(self):
        """Said again, the word covers the next merge, and a refusal after that one names it
        alone, not the merges the word before it made."""
        self.used_on_one()
        self.accept()
        self.assertNotEqual("deny", self.decision(self.merge(62)))
        self.open_pr(63, "feat/c")
        said = self.refused(63)
        self.assertIn("used on #62, and not this merge", said)
        self.assertNotIn("#61", said)


class TestAPullRequestOpenedAgainOnTheSameCommitIsCovered(WordCase):
    """#1121 was based on #1120's branch, and GitHub closed it when that branch went with its
    merge. The same branch and commit were opened again, against the trunk, as #1124. Nobody
    pushed anything: what the founder looked at is what is there."""

    def named_by_the_word(self) -> None:
        git(["branch", "feat/base", "main"], self.repo)
        self.write("src/app.py", "x = 1\n")
        self.commit("the work")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_pr(48, "feat/x", base="feat/base")
        self.accept()

    def test_it_is_covered_by_the_word_that_named_the_one_closed(self):
        self.named_by_the_word()
        self.open_pr(51, "feat/x")
        merged = self.merge(51)
        self.assertNotEqual("deny", self.decision(merged), self.reason(merged))

    def test_it_merges_once(self):
        self.named_by_the_word()
        self.open_pr(51, "feat/x")
        self.merge(51)
        self.assertEqual("deny", self.decision(self.merge(51)))

    def test_a_branch_that_moved_since_the_word_is_new_work(self):
        self.named_by_the_word()
        self.write("src/app.py", "x = 2\n")
        self.commit("more work after the word")
        evidence.record_green(self.ctx(), ["pytest"])
        self.open_pr(51, "feat/x")
        said = self.refused(51)
        self.assertIn("it names the pull request feat/x had before this one, and carries to one "
                      "opened since only where the branch is still at the commit they said it "
                      "about", said)

    def test_one_opened_again_against_the_same_base_is_new_work(self):
        """What a branch opens once its pull request merged where nothing here heard it, which
        reads as closed as well (#279): GitHub never closes one for its base and leaves it."""
        self.named_by_the_word()
        self.open_pr(51, "feat/x", base="feat/base")
        self.assertEqual("deny", self.decision(self.merge(51)))

    def test_a_branch_whose_pull_request_merged_is_new_work(self):
        """A branch that carries on after its merge gets a new pull request nobody has looked
        at (#279), whatever commit it is on."""
        self.named_by_the_word()
        self.assertNotEqual("deny", self.decision(self.merge(48)))
        self.open_pr(51, "feat/x")
        self.assertEqual("deny", self.decision(self.merge(51)))

    def test_one_merged_where_nothing_here_heard_it_is_not_carried_from(self):
        """Settled by the trunk's history, its entry stays in the pool: what a branch opens once
        its pull request merged is new work, whatever it is opened into (#279)."""
        self.named_by_the_word()
        pullrequest.settle(self.ctx(), "feat/x", pullrequest.MERGED)
        self.open_pr(51, "feat/x")
        self.assertEqual("deny", self.decision(self.merge(51)))

    def test_a_record_that_never_said_what_it_was_opened_into_carries_nothing(self):
        """Filed before this version, a record has only the trunk it is judged against."""
        self.named_by_the_word()
        record = pullrequest._records(self.ctx())["feat/x"]
        pullrequest._write(self.ctx(), {key: value for key, value in record.items()
                                        if key != "into"})
        self.open_pr(51, "feat/x")
        self.assertEqual("deny", self.decision(self.merge(51)))

    def test_the_word_is_recorded_with_the_commit_it_was_said_about(self):
        self.named_by_the_word()
        [record] = pullrequest.outstanding(self.ctx())
        self.assertEqual(self.ctx().head, record.get(pullrequest.MERGE_ACCEPTED_ON))


class TestAStackOpenedInAShell(WordCase):
    """The same, with `gh pr create` in a shell: the base it names is the one it is opened into,
    where the record only ever had the trunk."""

    def opened_in_a_shell(self, command: str, number: int) -> None:
        self.tool("Bash", {"command": command})
        self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": f"https://github.com/o/r/pull/{number}\n", "stderr": "",
                              "interrupted": False, "isImage": False},
        })

    def test_the_branch_below_is_what_it_is_opened_into(self):
        git(["branch", "feat/base", "main"], self.repo)
        self.start()
        self.opened_in_a_shell("gh pr create --fill --base feat/base", 48)
        [record] = pullrequest.outstanding(self.ctx())
        self.assertEqual(("feat/base", "main"), (record.get("into"), record.get("base")),
                         "what a merge is judged against stays as it was")

    def test_opened_again_into_the_trunk_it_is_covered(self):
        git(["branch", "feat/base", "main"], self.repo)
        self.write("src/app.py", "x = 1\n")
        self.commit("the work")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.opened_in_a_shell("gh pr create --fill --base feat/base", 48)
        self.accept()
        self.opened_in_a_shell("gh pr create --fill", 51)
        merged = self.merge(51)
        self.assertNotEqual("deny", self.decision(merged), self.reason(merged))


if __name__ == "__main__":
    unittest.main()
