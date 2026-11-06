"""The tree a chat's agent ran in is put away once it is spent (decision 0075).

Claude Code keeps the tree a WorktreeCreate hook made for a subagent with `isolation: worktree`
or for a workflow agent, and runs no removal for it: "Remove those worktrees yourself when the
work is done." This plugin's hook made every such tree and recorded it for no session the reaper
reads, so each one an agent ran in stayed until the founder ran `claude-bp sweep`.
"""

from __future__ import annotations

from pathlib import Path

from helpers import git

from test_gates import GateCase


def an_agent(status: str = "running") -> dict:
    return {"id": "a1", "type": "subagent", "status": status, "description": "refactor the importer"}


class AgentTreeCase(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        self.start()

    def an_agents_tree(self, name: str = "agent-a1b2", chat: str = "s1") -> Path:
        """What Claude Code asks of the hook when it spawns an agent in a worktree of its own."""
        made = self.gate("worktree-create", {"session_id": chat, "hook_event_name": "WorktreeCreate",
                                             "name": name})
        tree = Path(made.stdout.strip())
        self.assertTrue(tree.is_dir(), made.stderr)
        return tree


class TestASpentTreeIsPutAway(AgentTreeCase):
    def test_a_clean_tree_whose_work_is_in_the_trunk_goes_at_the_end_of_the_turn(self):
        tree = self.an_agents_tree()

        proc = self.stop()

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(tree.exists(), "the agent's tree stayed after its turn")

    def test_not_while_an_agent_of_this_chat_is_still_at_work(self):
        tree = self.an_agents_tree()
        self.stop(background_tasks=[an_agent()])
        self.assertTrue(tree.is_dir(), "a tree was taken from under a running agent")


class TestWhatKeepsAnAgentsTree(AgentTreeCase):
    def test_work_left_in_it(self):
        tree = self.an_agents_tree()
        (tree / "notes.md").write_text("what the agent found\n", encoding="utf-8")
        self.stop()
        self.assertTrue(tree.is_dir())

    def test_commits_the_trunk_does_not_have(self):
        tree = self.an_agents_tree()
        (tree / "src" / "importer.py").write_text("y = 2\n", encoding="utf-8")
        git(["add", "-A"], tree)
        git(["commit", "-qm", "the agent's refactor"], tree)
        self.stop()
        self.assertTrue(tree.is_dir(), "an agent's committed work that never reached the trunk")

    def test_another_chats_agent(self):
        tree = self.an_agents_tree(chat="s2")
        self.stop()
        self.assertTrue(tree.is_dir(), "a tree another chat's agent works in is not this one's")

    def test_the_founders_switch(self):
        self.configure(remove_finished_trees=False)
        tree = self.an_agents_tree()
        self.stop()
        self.assertTrue(tree.is_dir())
