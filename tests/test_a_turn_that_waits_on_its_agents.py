"""A turn that ends to wait for its own agents is not a finish (decision 0070).

Claude Code has run a spawned agent in the background by default since 2.1.232, and a session
that hands work to three of them ends its turn to wait for their reports. The Stop gate judged
that turn as a finish: it ran the suite over files the agents were still writing, filed
UNVERIFIED for work not done, withdrew the card the founder's message had opened as unplanned,
and handed back the leases and the tree under the agents. Stop's `background_tasks` says what
is still running, and exists, in Claude Code's words, to tell "session is done" from "session
is paused waiting for background work to wake it back up".

Only an agent's work counts. A background shell or a monitor may never end, and one left
running would spare every finish for good.
"""

from __future__ import annotations

from helpers import sid

from claude_bestpractice import evidence, plan, sessions, store

from test_gates import GateCase


def an_agent(description: str = "write the tests for total()", status: str = "running") -> dict:
    """One entry of Stop's `background_tasks`, as Claude Code writes it for a subagent."""
    return {"id": "a4d2c8f1e0b3a297", "type": "subagent", "status": status,
            "description": description, "agent_type": "general-purpose"}


class TestTheTurnWaitsForItsAgents(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.a_red_suite()
        self.commit("the suite this repository runs")
        self.start()
        self.card = self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "def total(prices):\n    return sum(prices)\n")

    def test_nothing_is_run_while_they_work(self):
        proc = self.stop(background_tasks=[an_agent()])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(0, self.ran(), "the suite ran over files the agent was still writing")
        self.assertIn("waiting, not finished — 1 agent(s) still at work "
                      "(write the tests for total())", proc.stderr)

    def test_the_finish_after_their_reports_is_judged(self):
        self.assertEqual(0, self.stop(background_tasks=[an_agent()]).returncode)

        proc = self.stop(background_tasks=[])

        self.assertEqual(2, proc.returncode, "the finish after the reports went unjudged")
        self.assertEqual(1, self.ran())

    def test_the_ceiling_files_nothing_while_they_work(self):
        """An UNVERIFIED mark stays on the branch until the founder accepts it, and nothing
        here is finished: the agents are still doing it."""
        for attempt in range(evidence.MAX_CONSECUTIVE_BLOCKS):
            self.assertEqual(2, self.stop(stop_hook_active=attempt > 0).returncode)

        proc = self.stop(stop_hook_active=True, background_tasks=[an_agent()])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("UNVERIFIED", proc.stderr)
        self.assertEqual([], store.read_jsonl(store.tier_b(self.ctx(), "unverified.jsonl")))

    def test_a_shell_left_running_spares_nothing(self):
        """`npm run dev` in the background never reports back; counted, it would spare every
        finish of the session for good."""
        server = {"id": "b1", "type": "shell", "status": "running", "description": "dev server",
                  "command": "npm run dev"}

        proc = self.stop(background_tasks=[server])

        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertEqual(1, self.ran())

    def test_an_agent_that_is_over_spares_nothing(self):
        proc = self.stop(background_tasks=[an_agent(status="completed")])

        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertEqual(1, self.ran())


class TestOnlyWorkUnderWaySparesAFinish(TestTheTurnWaitsForItsAgents):
    """An idle teammate stays `running` in the list for good (anthropics/claude-code#85955), and an
    entry with no status said nothing: either spared every finish of the session after it."""

    def test_an_idle_teammate_spares_nothing(self):
        mate = {"id": "t6edmim5e", "type": "teammate", "status": "running", "name": "researcher",
                "description": "Reply with exactly the word: done."}
        proc = self.stop(background_tasks=[mate])
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertEqual(1, self.ran())

    def test_an_agent_whose_status_says_nothing_spares_nothing(self):
        silent = {"id": "a4d2c8f1e0b3a297", "type": "subagent", "description": "write the tests"}
        proc = self.stop(background_tasks=[silent])
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertEqual(1, self.ran())


class TestLandedWorkIsNotAskedForACard(GateCase):
    def test_while_an_agent_researches(self):
        """The demand fixed in #149 came back for any turn waiting on an agent."""
        self.start()
        self.write("feature.py", "x = 1\n")
        self.commit("the work, finished and on the trunk")
        proc = self.stop(background_tasks=[an_agent("research the newest agent harnesses")])
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("Nothing on the board", proc.stderr)


class TestWhatTheOthersAreOwedStillHolds(GateCase):
    """The siblings read the board to decide what is safe to touch, and work under no card is
    work none of them can see, the agents' included."""

    def test_work_no_card_names_is_still_refused(self):
        self.a_red_suite()
        self.commit("the suite this repository runs")
        self.start()
        self.write("feature.py", "def total(prices):\n    return sum(prices)\n")

        proc = self.stop(background_tasks=[an_agent()])

        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("Nothing on the board says this session is working", proc.stderr)
        self.assertEqual(0, self.ran(), "the suite is a finish's, and this is no finish")

    def test_asked_again_and_ignored_it_files_nothing(self):
        """The ceiling ends the loop and makes no finish of it: UNVERIFIED stays on a branch
        until the founder accepts it, and the card is asked for again at the next finish."""
        self.a_red_suite()
        self.commit("the suite this repository runs")
        self.start()
        self.write("feature.py", "def total(prices):\n    return sum(prices)\n")
        for attempt in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1):
            proc = self.stop(stop_hook_active=attempt > 0, background_tasks=[an_agent()])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("UNVERIFIED", proc.stderr)
        self.assertEqual([], store.read_jsonl(store.tier_b(self.ctx(), "unverified.jsonl")))


class TestTheirLeasesAreKept(GateCase):
    """A finish hands back what the session holds; agents still writing hold it with it."""

    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.held = sid(self.repo, "s1")
        sessions.acquire_lease(self.ctx(), self.held, "feature.py")

    def test_while_they_work(self):
        self.assertEqual(0, self.stop(background_tasks=[an_agent()]).returncode)
        self.assertEqual(["feature.py"], sessions.leases_held_by(self.ctx(), self.held))

    def test_and_handed_back_at_the_finish_after_them(self):
        self.stop(background_tasks=[an_agent()])
        self.assertEqual(0, self.stop(background_tasks=[]).returncode)
        self.assertEqual([], sessions.leases_held_by(self.ctx(), self.held))


class TestTheCardTheFounderOpenedIsNotWithdrawn(GateCase):
    """The card a message opens is planned by the work that follows it, and that work is the
    agents' when the session hands it to them and ends the turn to wait."""

    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.gate("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                     "prompt": "исследуй новые харнессы и возьми лучшее в плагин"})
        self.card = plan.load_all(self.ctx(), plan.NEXT)[0]

    def test_it_stays_on_the_board_while_they_work(self):
        self.stop(background_tasks=[an_agent("research the newest agent harnesses")])
        self.assertEqual([self.card.id], [card.id for card in plan.load_all(self.ctx(), plan.NEXT)])

    def test_without_them_a_turn_that_planned_nothing_still_withdraws_it(self):
        self.stop(background_tasks=[])
        self.assertEqual([], plan.load_all(self.ctx(), plan.NEXT))
