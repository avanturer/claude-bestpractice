"""Issue #295: one `+merge` merged the first of two pull requests the session had opened and
named, and refused the second.

The session opened #1139 and #1140 itself with `gh pr create`, named both by number and link in
its message, and was answered `+merge`. #1139 merged; #1140 was refused, "found on GitHub, opened
by no session here", and the founder had to say the word a second time. The refusal had promised
that the word allows "every pull request you have open, and every one you showed them by number
since their last message". #1140 was both, and the word counted it as neither.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from unittest import mock

from helpers import git, sid
from test_pullrequest import PRCase

from claude_bestpractice import evidence, pullrequest

LINK = "https://github.com/o/r/pull/{}"


def github_time(moment: float) -> str:
    """A moment as `gh pr list --json createdAt` writes it."""
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TwoPullRequestsCase(PRCase):
    def setUp(self) -> None:
        super().setUp()
        git(["remote", "add", "origin", "https://github.com/o/r.git"], self.repo)
        self.stubs = self.tmp / "gh-stub"
        self.stubs.mkdir()
        (self.stubs / "gh").write_text("#!/bin/sh\nprintf '%s' \"$FAKE_GH_LIST\"\n")
        (self.stubs / "gh").chmod(0o755)

    def github_has(self, listed: list[dict]) -> None:
        """A `gh` first on PATH, answering `pr list` with `listed`."""
        patched = mock.patch.dict(os.environ, {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_GH_LIST": json.dumps(listed),
        })
        patched.start()
        self.addCleanup(patched.stop)

    def committed(self, text: str) -> None:
        self.write("src/app.py", text)
        self.commit(text)
        evidence.record_green(self.ctx(), ["pytest"])

    def opened_in_a_shell(self, command: str, number: int) -> None:
        """`gh pr create` as the harness runs it: the request, and the response that ran."""
        self.tool("Bash", {"command": command})
        self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": f"{LINK.format(number)}\n", "stderr": ""},
        })

    def stop(self, message: str = "", session_id: str = "s1"):
        event = {"session_id": session_id, "hook_event_name": "Stop", "stop_hook_active": False}
        return self.gate("evidence-gate", {**event, "last_assistant_message": message}
                         if message else event)

    def merge(self, number: int):
        return self.ran("Bash", {"command": f"gh pr merge {number} --squash"})

    def merged(self, number: int) -> None:
        proc = self.merge(number)
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def record(self, branch: str) -> dict:
        return pullrequest._records(self.ctx())[branch]


class TestThePullRequestTheSessionOpenedIsItsOwn(TwoPullRequestsCase):
    """Condition one: #1140 was opened by this session. A line that makes its branch and opens
    the pull request in one go is read before it runs, on the branch the session stood on."""

    def first_then_second(self, second: str) -> None:
        self.start()
        self.committed("x = 1\n")
        self.opened_in_a_shell("gh pr create --fill", 1139)
        # The second is opened on a line that makes its branch first, and the call as a whole
        # failed after `gh` had opened it, so no response carried its number (decision 0038).
        self.tool("Bash", {"command": second})
        git(["checkout", "-q", "-b", "fix/timeout"], self.repo)
        self.committed("x = 2\n")

    def test_the_report(self):
        self.first_then_second("git checkout -b fix/timeout && gh pr create --fill && false")
        self.github_has([{"number": 1140, "url": LINK.format(1140), "isDraft": False,
                          "baseRefName": "main", "createdAt": github_time(time.time())}])
        self.stop("Opened #1139 and #1140. Say +merge.")
        self.accept()
        self.merged(1139)
        self.merged(1140)

    def test_the_branch_the_line_makes_is_the_one_it_opens_from(self):
        self.first_then_second("git checkout -b fix/timeout && gh pr create --fill && false")
        record = self.record("fix/timeout")
        self.assertEqual(sid(self.repo, "s1"), record["session_id"])
        self.assertEqual(1139, self.record("feat/x")["number"], "the first one stays its own")

    def test_one_on_a_branch_it_cannot_name_goes_on_no_record(self):
        """`git checkout "$B"` names no branch here: the one the session stood on is the one
        certainly wrong."""
        self.start()
        self.opened_in_a_shell("gh pr create --fill", 1139)
        pullrequest.settle(self.ctx(), "feat/x", pullrequest.MERGED)
        before = self.record("feat/x")
        self.tool("Bash", {"command": 'git checkout "$B" && gh pr create --fill'})
        self.assertEqual(before, self.record("feat/x"), "a merged branch was given a new one")

    def test_github_says_whose_it_is_where_no_hook_could(self):
        """However the record went missing, the chat that ran `gh pr create` in this tree just
        before GitHub created the pull request is the chat that opened it."""
        self.first_then_second('B=fix/timeout; git checkout -b "$B" && gh pr create --fill')
        self.github_has([{"number": 1140, "url": LINK.format(1140), "isDraft": False,
                          "baseRefName": "main", "createdAt": github_time(time.time())}])
        self.stop()
        self.assertEqual(sid(self.repo, "s1"), self.record("fix/timeout")["session_id"])
        self.accept()
        self.merged(1139)
        self.merged(1140)

    def test_one_github_created_long_before_is_still_no_sessions(self):
        self.first_then_second('B=fix/timeout; git checkout -b "$B" && gh pr create --fill')
        self.github_has([{"number": 1140, "url": LINK.format(1140), "isDraft": False,
                          "baseRefName": "main", "createdAt": github_time(time.time() - 7200)}])
        self.stop()
        self.assertEqual("", self.record("fix/timeout")["session_id"])

    def test_another_chats_opening_is_not_this_ones(self):
        self.first_then_second('B=fix/timeout; git checkout -b "$B" && gh pr create --fill')
        self.github_has([{"number": 1140, "url": LINK.format(1140), "isDraft": False,
                          "baseRefName": "main", "createdAt": github_time(time.time())}])
        self.stop(session_id="s2")
        self.assertEqual("", self.record("fix/timeout")["session_id"])


class TestThePullRequestTheMessageNamedIsNamedByTheWord(TwoPullRequestsCase):
    """Condition two: #1140 was named, by number and link, in the message the founder answered."""

    def found_on_github(self, number: int = 1140, branch: str = "fix/timeout") -> None:
        """One no hook here saw opened, as the Stop gate records what GitHub has (#239)."""
        git(["branch", branch], self.repo)
        pullrequest.opened(self.ctx(), branch, "main", "", number=number, url=LINK.format(number))

    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.committed("x = 1\n")
        self.open_a_pr()

    def test_a_link_in_the_message_names_it(self):
        self.found_on_github()
        self.stop(f"Opened #{self.PR_NUMBER} and the timeout fix ({LINK.format(1140)}).")
        self.accept()
        self.merged(self.PR_NUMBER)
        self.merged(1140)
        self.assertEqual("deny", self.decision(self.merge(1140)), "each merges once")

    def test_a_number_on_record_here_names_it(self):
        self.found_on_github()
        self.stop("PR #1140 is the companion fix.")
        self.accept()
        self.merged(1140)

    def test_a_number_nothing_here_records_is_no_pull_request_of_theirs(self):
        """`#295` is as often the issue: a word naming only that would lose the one merge."""
        self.assertEqual([], pullrequest.named_in_message(self.ctx(), "Fixes #295."))

    def test_another_repositorys_pull_request_is_not_named(self):
        self.found_on_github()
        for text in ("see https://github.com/other/x/pull/1140", "see other/x#1140"):
            with self.subTest(text=text):
                self.assertEqual([], pullrequest.named_in_message(self.ctx(), text))
        for text in ("o/r#1140", "PR#1140", "(#1140)"):
            with self.subTest(text=text):
                self.assertEqual([1140], pullrequest.named_in_message(self.ctx(), text))

    def test_what_is_not_a_number_of_its_own_is_not_named(self):
        self.found_on_github()
        for text in (f"{LINK.format(48)}#issuecomment-1140", "colour &#1140;", "2#1140"):
            with self.subTest(text=text):
                self.assertNotIn(1140, pullrequest.named_in_message(self.ctx(), text))

    def test_a_message_before_their_previous_one_names_nothing(self):
        self.found_on_github()
        self.stop(f"Opened {LINK.format(1140)}.")
        self.gate("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                     "prompt": "а что с CI?"})
        self.stop("CI is green.")
        self.accept()
        self.assertEqual("deny", self.decision(self.merge(1140)))

    def test_another_chats_message_names_nothing_here(self):
        self.found_on_github()
        self.stop(f"Opened {LINK.format(1140)}.", session_id="s2")
        self.accept()
        self.assertEqual("deny", self.decision(self.merge(1140)))

    def test_the_refusal_says_what_counts(self):
        self.found_on_github()
        said = self.reason(self.merge(1140))
        self.assertIn("named in the message they answer by its link", said)


class TestALineThatSwitchesBranches(TwoPullRequestsCase):
    def branch(self, command: str) -> str:
        return pullrequest.opened_from(self.ctx(), "Bash", command, {}, str(self.repo))

    def test_the_branch_it_makes_or_moves_to(self):
        git(["branch", "fix/known"], self.repo)
        for command, expected in (
                ("git checkout -b fix/a && gh pr create --fill", "fix/a"),
                ("git checkout -q -B fix/a origin/main && gh pr create", "fix/a"),
                ("git switch -c fix/a && gh pr create", "fix/a"),
                ("git switch --create=fix/a && gh pr create", "fix/a"),
                ("git checkout fix/known && gh pr create", "fix/known"),
                ("git switch fix/known; gh pr create", "fix/known")):
            with self.subTest(command=command):
                self.assertEqual(expected, self.branch(command))

    def test_a_checkout_of_files_moves_no_branch(self):
        for command in ("git checkout -- src/app.py && gh pr create",
                        "git checkout src/app.py && gh pr create",
                        "gh pr create && git checkout -b later"):
            with self.subTest(command=command):
                self.assertEqual("feat/x", self.branch(command))

    def test_one_it_cannot_name_is_none(self):
        for command in ('git checkout "$B" && gh pr create', "git checkout - && gh pr create",
                        "git switch --detach main && gh pr create"):
            with self.subTest(command=command):
                self.assertEqual("", self.branch(command))

    def test_a_cd_after_the_switch_leaves_it_behind(self):
        tree = self.tmp / "other"
        git(["worktree", "add", "-q", "-b", "feat/other", str(tree)], self.repo)
        self.assertEqual("feat/other", self.branch(
            f"git checkout -b fix/a && cd {tree} && gh pr create"))
